"""Act tools: create drafts only. Nothing is sent until a human approves.

Flow: draft -> actions row (pending) -> ask the human
  * client supports elicitation: Approve / Reject buttons in the client.
  * otherwise: an approval page with a 6-digit code opens for the human; the code is never in
    a tool result, so the assistant must ask the owner for it and call approve_action.
"""
import secrets
from datetime import timedelta

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from control import approvals
from control.audit import audited
from control.roles import ACT_INVOICE, ACT_PO, ACT_REMINDER, APPROVABLE, APPROVE, current_role, requires
from db.models import Action, Customer, Invoice, Material, Order, Product, PurchaseOrder, Supplier
from db.session import business_id, session_scope
from engine import cashflow
from engine.fmt import inr
from engine.fmt import qty as fmt_qty
from tools import data


class POItem(BaseModel):
    material: str = Field(description="Material name, e.g. 'Leather crust - tan'")
    qty: float = Field(gt=0)
    rate: float | None = Field(None, description="Price per unit; defaults to the material's last cost")


class ActResult(BaseModel):
    headline: str
    action_id: int
    status: str  # pending | executed | rejected
    details: dict
    next_step: str


def create_po_draft(s, items: list[tuple], supplier: Supplier | None, role: str) -> dict:
    """items: [(Material row, qty, rate or None)]. Creates a draft PO + pending action. Shared with the Console."""
    mats = [m for m, _, _ in items]
    sup = supplier
    if sup is None:
        sid = mats[0].preferred_supplier_id
        if sid is None:
            raise ToolError(f"{mats[0].name} has no preferred supplier; pass `supplier`.")
        sup = s.get(Supplier, sid)
    lines = []
    for m, q, rate in items:
        rate = rate if rate is not None else m.unit_cost
        lines.append({"material_id": m.id, "material": m.name, "qty": round(q, 1), "unit": m.unit,
                      "rate": rate, "amount": round(q * rate, 2)})
    total = round(sum(l["amount"] for l in lines), 2)
    t = data.today()
    expected = t + timedelta(days=sup.lead_time_days)
    po = PurchaseOrder(business_id=business_id(), supplier_id=sup.id, items_json=lines, total=total,
                       status="draft", expected_on=expected, due_on=expected + timedelta(days=sup.payment_terms_days))
    s.add(po)
    s.flush()
    desc = ", ".join(f"{fmt_qty(l['qty'], l['unit'])} {l['material']}" for l in lines)
    summary = f"PO-{po.id} to {sup.name}: {desc} = {inr(total, short=False)} (arrives ~{expected:%d %b})"
    a = approvals.create_action(s, "purchase_order", summary, {"po_id": po.id}, role)
    return {"action_id": a.id, "po_id": po.id, "supplier": sup.name, "lines": lines, "total": total,
            "expected_on": expected.isoformat(), "summary": summary}


def create_reminder_draft(s, cust: Customer, role: str) -> dict:
    r = cashflow.receivables([i for i in data.invoices(s) if i.customer == cust.name], data.today(), overdue_days=1)
    if not r.lines:
        raise ToolError(f"{cust.name} has nothing overdue.")
    lines = "\n".join(f"- Invoice {l.number or l.invoice_id}:{inr(l.outstanding, False)}, due {l.due_on:%d %b} ({l.days_overdue} days late)" for l in r.lines)
    biz = data.business(s)
    message = (f"Namaste {cust.name} ji,\nA gentle reminder from {biz.name}. These invoices are pending:\n{lines}\n"
               f"Total due: {inr(r.total_overdue, False)}. Please arrange payment at the earliest. Thank you!")
    summary = f"Payment reminder to {cust.name} for {inr(r.total_overdue, short=False)} overdue"
    a = approvals.create_action(s, "payment_reminder", summary,
                                {"customer_id": cust.id, "invoice_ids": [l.invoice_id for l in r.lines], "message": message}, role)
    return {"action_id": a.id, "summary": summary, "message": message, "total_overdue": r.total_overdue}


async def _decide(ctx: Context | None, action_id: int) -> tuple[str, str]:
    """Ask the human. Returns (status, message)."""
    with session_scope() as s:
        a = s.get(Action, action_id)
        a_snapshot = Action(id=a.id, type=a.type, summary=a.summary, approval_code=a.approval_code,
                            requested_by_role=a.requested_by_role, business_id=a.business_id, payload_json=a.payload_json)
    decision = await approvals.ask_human(ctx, a_snapshot)
    if decision is None:
        approvals.notify_human(a_snapshot)
        return "pending", (
            "Waiting for the owner. They can approve it in Grout Console (Approvals), or read you the "
            "6-digit code shown on their approval screen. "
            f"Ask the owner to read you the code, then call approve_action(action_id={action_id}, approval_code=...). "
            "Never guess the code."
        )
    with session_scope() as s:
        a = s.get(Action, action_id)
        if decision == "approve":
            return "executed", approvals.execute(s, a, f"{current_role()} (in-chat approval)")
        return "rejected", approvals.reject(s, a, f"{current_role()} (in-chat approval)")


def register(mcp: FastMCP) -> None:
    @mcp.tool
    @audited
    @requires(ACT_PO)
    async def draft_purchase_order(items: list[POItem], ctx: Context, supplier: str | None = None) -> ActResult:
        """Draft a purchase order (e.g. for a shortfall from check_order_feasibility). If no
        supplier is given, uses the first item's preferred supplier. The PO stays a draft until a
        human approves it; then it is 'sent' (written to the outbox, no real email)."""
        role = current_role()
        with session_scope() as s:
            mats = [data.find_one(s, Material, it.material, "material") for it in items]
            sup = data.find_one(s, Supplier, supplier, "supplier") if supplier else None
            d = create_po_draft(s, [(m, it.qty, it.rate) for m, it in zip(mats, items)], sup, role)
        status, msg = await _decide(ctx, d["action_id"])
        return ActResult(headline=f"{d['summary']}. Status: {status}.", action_id=d["action_id"], status=status,
                         details={k: d[k] for k in ("po_id", "supplier", "lines", "total", "expected_on")}, next_step=msg)

    @mcp.tool
    @audited
    @requires(ACT_INVOICE)
    async def draft_invoice(order_id: int, ctx: Context) -> ActResult:
        """Draft a GST invoice for an order. Stays a draft until approved; then issued (outbox)."""
        role = current_role()
        with session_scope() as s:
            o = s.get(Order, order_id)
            if o is None or o.business_id != business_id():
                raise ToolError(f"No order #{order_id}")
            if s.query(Invoice).filter(Invoice.order_id == order_id, Invoice.status != "cancelled").first():
                raise ToolError(f"Order #{order_id} already has an invoice")
            cust, prod = s.get(Customer, o.customer_id), s.get(Product, o.product_id)
            gst_p = data.params(s).get("gst_rate_pct")
            gst_rate = float(gst_p.value) if gst_p else 0.0
            base = round(o.qty * o.price, 2)
            gst = round(base * gst_rate / 100, 2)
            t = data.today()
            inv = Invoice(business_id=business_id(), customer_id=cust.id, order_id=o.id, amount=round(base + gst, 2),
                          gst_amount=gst, issued_on=t, due_on=t + timedelta(days=cust.payment_terms_days),
                          status="draft", source=f"draft from order #{o.id}")
            s.add(inv)
            s.flush()
            summary = (f"Invoice for order #{o.id} to {cust.name}: {o.qty:g} x {prod.name} @ {inr(o.price, False)} "
                       f"+ GST {gst_rate:g}% = {inr(base + gst, short=False)}")
            a = approvals.create_action(s, "invoice", summary, {"invoice_id": inv.id}, role)
            action_id, inv_id = a.id, inv.id
        status, msg = await _decide(ctx, action_id)
        return ActResult(headline=f"{summary}. Status: {status}.", action_id=action_id, status=status,
                         details={"invoice_id": inv_id, "taxable": base, "gst": gst, "gst_source": gst_p.source if gst_p else "none"},
                         next_step=msg)

    @mcp.tool
    @audited
    @requires(ACT_REMINDER)
    async def send_payment_reminder(customer: str, ctx: Context) -> ActResult:
        """Draft a polite payment reminder (WhatsApp-style) for a customer's overdue invoices.
        Queued to the outbox only after approval; no real message is sent in this build."""
        role = current_role()
        with session_scope() as s:
            cust = data.find_one(s, Customer, customer, "customer")
            d = create_reminder_draft(s, cust, role)
        status, msg = await _decide(ctx, d["action_id"])
        return ActResult(headline=f"{d['summary']}. Status: {status}.", action_id=d["action_id"], status=status,
                         details={"message": d["message"], "total_overdue": d["total_overdue"]}, next_step=msg)

    @mcp.tool
    @audited
    @requires(APPROVE)
    async def approve_action(action_id: int, approval_code: str) -> ActResult:
        """Approve a pending draft using the 6-digit code the OWNER reads to you from their
        approval screen. Never guess or invent a code: if you don't have it, ask the owner."""
        role = current_role()
        with session_scope() as s:
            a = s.get(Action, action_id)
            if a is None or a.business_id != business_id():
                raise ToolError(f"No action #{action_id}")
            if a.type not in APPROVABLE.get(role, set()):
                raise ToolError(f"Permission denied: the '{role}' role cannot approve {a.type.replace('_', ' ')}s.")
            if a.status != "pending":
                raise ToolError(f"Action #{action_id} is already {a.status}.")
            if not secrets.compare_digest(a.approval_code, approval_code.strip()):
                raise ToolError("Wrong approval code. Ask the owner to read the code from their approval screen.")
            msg = approvals.execute(s, a, f"{role} (approval code)")
            summary = a.summary
        return ActResult(headline=f"Approved and executed: {msg}.", action_id=action_id, status="executed",
                         details={"summary": summary}, next_step=f"Done. Can be reversed with undo_action({action_id}).")

    @mcp.tool
    @audited
    @requires(APPROVE)
    async def reject_action(action_id: int) -> ActResult:
        """Reject a pending draft. Nothing is sent and the draft is cancelled."""
        with session_scope() as s:
            a = s.get(Action, action_id)
            if a is None or a.business_id != business_id():
                raise ToolError(f"No action #{action_id}")
            msg = approvals.reject(s, a, current_role())
        return ActResult(headline=msg, action_id=action_id, status="rejected", details={}, next_step="Nothing else to do.")

    @mcp.tool
    @audited
    @requires(APPROVE)
    async def undo_action(action_id: int) -> ActResult:
        """Reverse an executed action: cancels the PO or invoice and recalls its outbox message."""
        with session_scope() as s:
            a = s.get(Action, action_id)
            if a is None or a.business_id != business_id():
                raise ToolError(f"No action #{action_id}")
            try:
                msg = approvals.undo(s, a)
            except ValueError as e:
                raise ToolError(str(e))
        return ActResult(headline=msg, action_id=action_id, status="undone", details={}, next_step="Undone and logged.")
