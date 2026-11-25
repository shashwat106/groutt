"""Human approval for Act tools, plus execute / reject / undo.

Design rule: the AI must never be able to approve its own draft.
  * If the client supports MCP elicitation, the human clicks Approve / Reject in the client.
  * Otherwise a 6-digit approval code is shown ONLY to the human (an approval page opened on
    this machine, and the console `uv run python -m control.approvals`). The code is never in
    any tool result, so the AI has to ask the human for it before `approve_action` will work.

Run as a script to act as the owner's approval console:
    uv run python -m control.approvals            # list pending actions and their codes
    uv run python -m control.approvals approve 3  # approve action 3 directly
    uv run python -m control.approvals reject 3
"""
from __future__ import annotations

import html
import logging
import os
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from db.models import Action, Customer, Invoice, Outbox, PurchaseOrder, Supplier
from db.session import ROOT, business_id, session_scope

log = logging.getLogger("industryos.approvals")
OUT = ROOT / "out"


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def create_action(sess: Session, type_: str, summary: str, payload: dict, role: str) -> Action:
    a = Action(
        business_id=business_id(),
        type=type_,
        summary=summary,
        payload_json=payload,
        status="pending",
        approval_code=new_code(),
        requested_by_role=role,
    )
    sess.add(a)
    sess.flush()
    return a


def notify_human(action: Action) -> Path:
    """Show the approval code to the human only: stderr (server log) + a local approval page."""
    try:
        print(
            f"[Grout] APPROVAL NEEDED: action #{action.id} ({action.type}) - {action.summary} - "
            f"code {action.approval_code}",
            file=sys.stderr,
        )
    except Exception:
        pass
    OUT.mkdir(exist_ok=True)
    page = OUT / f"approval_{action.id}.html"
    page.write_text(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Approve action #{action.id}</title>
<style>body{{font-family:system-ui,sans-serif;background:#f6f3ee;color:#222;display:grid;place-items:center;min-height:100vh;margin:0}}
.card{{background:#fff;border-radius:16px;padding:32px 40px;box-shadow:0 8px 30px #0002;max-width:520px}}
.code{{font-size:48px;letter-spacing:10px;font-weight:700;color:#7a3e12;margin:16px 0}}
small{{color:#666}}</style></head><body><div class="card">
<small>Grout - owner approval</small>
<h2>Approve: {html.escape(action.summary)}</h2>
<p>Action #{action.id} - {html.escape(action.type.replace('_', ' '))} - requested by {html.escape(action.requested_by_role)}</p>
<div class="code">{action.approval_code}</div>
<p>To approve, tell the assistant this code. To reject, just say no.<br>
<small>Or run <code>uv run python -m control.approvals approve {action.id}</code></small></p>
</div></body></html>""",
        encoding="utf-8",
    )
    # If the owner uses Grout Console, approvals show up there live; no pop-up needed.
    if os.environ.get("INDUSTRYOS_OPEN_BROWSER", "1") == "1" and not os.environ.get("INDUSTRYOS_CONSOLE_URL"):
        try:
            webbrowser.open(page.as_uri())
        except Exception:  # headless machines
            pass
    return page


def client_supports_elicitation(ctx) -> bool:
    try:
        caps = ctx.session.client_params.capabilities
        return getattr(caps, "elicitation", None) is not None
    except Exception:
        return False


async def ask_human(ctx, action: Action) -> str | None:
    """Ask via elicitation. Returns 'approve', 'reject', or None when elicitation isn't available."""
    if ctx is None or not client_supports_elicitation(ctx):
        return None
    try:
        res = await ctx.elicit(
            f"Approve this action?\n\n{action.summary}\n\n(action #{action.id})",
            ["Approve", "Reject"],
        )
    except Exception as e:  # client advertised but failed
        log.warning("elicitation failed: %s", e)
        return None
    if getattr(res, "action", None) == "accept" and getattr(res, "data", None) == "Approve":
        return "approve"
    return "reject"


# ---------- execute / reject / undo ----------

def _outbox(sess: Session, action: Action, channel: str, to: str, subject: str, body: str) -> None:
    sess.add(Outbox(business_id=action.business_id, action_id=action.id, channel=channel,
                    recipient=to, subject=subject, body=body))
    log.info("outbox %s -> %s: %s", channel, to, subject)


def execute(sess: Session, action: Action, approver: str) -> str:
    if action.status != "pending":
        raise ValueError(f"Action #{action.id} is {action.status}, not pending")
    p = action.payload_json
    if action.type == "purchase_order":
        po = sess.get(PurchaseOrder, p["po_id"])
        sup = sess.get(Supplier, po.supplier_id)
        po.status = "sent"
        lines = "\n".join(f"- {i['material']}: {i['qty']} {i['unit']} @ Rs {i['rate']}" for i in po.items_json)
        _outbox(sess, action, "email", sup.email or sup.phone or sup.name, f"Purchase order PO-{po.id}",
                f"Dear {sup.name},\nPlease supply:\n{lines}\nTotal: Rs {po.total:,.0f}\nExpected by {po.expected_on}.")
        result = f"PO-{po.id} approved and sent to {sup.name} (outbox)"
    elif action.type == "invoice":
        inv = sess.get(Invoice, p["invoice_id"])
        cust = sess.get(Customer, inv.customer_id)
        inv.status = "open"
        inv.number = inv.number or f"INV-{inv.id:04d}"
        _outbox(sess, action, "email", cust.email or cust.name, f"Invoice {inv.number}",
                f"Dear {cust.name},\nPlease find invoice {inv.number} for Rs {inv.amount:,.0f}, due {inv.due_on}.")
        result = f"Invoice {inv.number} issued to {cust.name} (outbox)"
    elif action.type == "payment_reminder":
        cust = sess.get(Customer, p["customer_id"])
        _outbox(sess, action, "whatsapp", cust.phone or cust.name, "Payment reminder", p["message"])
        result = f"Payment reminder queued for {cust.name} (outbox)"
    elif action.type == "supplier_query":
        sup = sess.get(Supplier, p["supplier_id"])
        _outbox(sess, action, "email", sup.email or sup.phone or sup.name, p.get("subject", "Query on your bill"), p["message"])
        result = f"Bill query sent to {sup.name} (outbox)"
    else:
        raise ValueError(f"Unknown action type {action.type}")
    action.status = "executed"
    action.approved_by_role = approver
    action.decided_at = datetime.now(timezone.utc)
    return result


def reject(sess: Session, action: Action, by: str) -> str:
    if action.status != "pending":
        raise ValueError(f"Action #{action.id} is {action.status}, not pending")
    p = action.payload_json
    if action.type == "purchase_order":
        sess.get(PurchaseOrder, p["po_id"]).status = "cancelled"
    elif action.type == "invoice":
        sess.get(Invoice, p["invoice_id"]).status = "cancelled"
    action.status = "rejected"
    action.approved_by_role = by
    action.decided_at = datetime.now(timezone.utc)
    return f"Action #{action.id} rejected; nothing was sent"


def undo(sess: Session, action: Action) -> str:
    if action.status != "executed":
        raise ValueError(f"Only executed actions can be undone; #{action.id} is {action.status}")
    p = action.payload_json
    for msg in sess.query(Outbox).filter_by(action_id=action.id, status="queued"):
        msg.status = "recalled"
    if action.type == "purchase_order":
        po = sess.get(PurchaseOrder, p["po_id"])
        po.status = "cancelled"
        result = f"PO-{po.id} cancelled and its outbox message recalled"
    elif action.type == "invoice":
        inv = sess.get(Invoice, p["invoice_id"])
        inv.status = "cancelled"
        result = f"Invoice {inv.number} cancelled and its outbox message recalled"
    else:
        result = "Message recalled from the outbox"
    action.status = "undone"
    return result


# ---------- console ----------

def _console(argv: list[str]) -> None:
    with session_scope() as s:
        if len(argv) >= 2 and argv[0] in ("approve", "reject"):
            a = s.get(Action, int(argv[1]))
            if a is None:
                print("No such action")
                return
            print(execute(s, a, "owner (console)") if argv[0] == "approve" else reject(s, a, "owner (console)"))
            return
        pending = s.query(Action).filter_by(business_id=business_id(), status="pending").all()
        if not pending:
            print("No actions waiting for approval.")
        for a in pending:
            print(f"#{a.id}  {a.type:<17} code {a.approval_code}   {a.summary}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _console(sys.argv[1:])
