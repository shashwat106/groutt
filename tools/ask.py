"""Ask tools (read-only). Every number comes from the engine; each answer lists its sources."""
from datetime import timedelta

from fastmcp import FastMCP
from pydantic import BaseModel

from control.audit import audited
from control.roles import ASK, requires
from db.models import Action, AuditLog
from db.session import business_id, session_scope
from engine import cashflow, costing, stock
from engine.fmt import inr, qty
from tools import data

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


class CostAnswer(BaseModel):
    headline: str
    breakdown: costing.CostBreakdown
    sources: list[str]


class StockAnswer(BaseModel):
    headline: str
    status: stock.StockStatus
    sources: list[str]


class ReceivablesAnswer(BaseModel):
    headline: str
    receivables: cashflow.ReceivablesResult
    sources: list[str]


class WeekPoint(BaseModel):
    week_ending: str
    balance: float


class CashAnswer(BaseModel):
    headline: str
    opening_balance: float
    closing_balance: float
    lowest_balance: float
    lowest_on: str
    first_shortfall_date: str | None
    weekly: list[WeekPoint]
    assumptions: list[str]
    sources: list[str]


class PendingAction(BaseModel):
    action_id: int
    type: str
    summary: str
    requested_by_role: str
    created_at: str


class PendingAnswer(BaseModel):
    headline: str
    actions: list[PendingAction]


class AuditRow(BaseModel):
    id: int
    at: str
    tool: str
    role: str
    ok: bool
    result: str


class AuditAnswer(BaseModel):
    headline: str
    rows: list[AuditRow]


def cost_for(s, product_name: str) -> costing.CostBreakdown:
    p = data.find_one(s, data.Product, product_name, "product")
    return costing.unit_cost(data.product_in(p), data.bom(s), data.materials(s), data.routing(s),
                             data.labour(s), data.overheads(s), data.params(s))


def cash_for(s, days: int) -> cashflow.ForecastResult:
    opening, src = data.cash_today(s)
    return cashflow.forecast(data.invoices(s), data.purchase_orders(s), data.overheads(s), days, opening,
                             data.today(), opening_source=src)


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def true_cost_per_unit(product: str) -> CostAnswer:
        """True cost of making one unit of a product: material, wastage, labour, overhead and
        rejection allowance, with margin at the current selling price. Read-only."""
        with session_scope() as s:
            c = cost_for(s, product)
        head = (f"{c.product}: true cost {inr(c.total, short=False)} per pair "
                f"(material {inr(c.material, False)}, wastage {inr(c.wastage, False)}, labour {inr(c.labour, False)}, "
                f"overhead {inr(c.overhead, False)}, rejection {inr(c.rejection_allowance, False)}). "
                f"Selling price {inr(c.selling_price, False)} -> margin {inr(c.margin_per_unit, False)} ({c.margin_pct}%).")
        return CostAnswer(headline=head, breakdown=c, sources=c.sources)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def stock_status(item: str | None = None) -> StockAnswer:
        """Stock on hand, what open orders already reserve, what's free, and items below reorder
        level. Pass `item` to look at one material. Read-only."""
        with session_scope() as s:
            st = stock.status(data.materials(s), data.bom(s), data.open_orders(s), data.params(s))
        if item:
            st.items = [i for i in st.items if item.lower() in i.material.lower()]
        low = [i for i in st.items if i.status != "ok"]
        head = (f"{len(st.items)} item(s), stock value {inr(st.total_value)}. "
                + (f"{len(low)} need attention: " + "; ".join(f"{i.material} {i.status} (free {qty(i.free, i.unit)})" for i in low)
                   if low else "Nothing below reorder level."))
        return StockAnswer(headline=head, status=st, sources=st.sources)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def receivables(overdue_days: int = 0) -> ReceivablesAnswer:
        """Money customers owe. overdue_days=0 lists every unpaid invoice; e.g. 30 lists only
        invoices at least 30 days late. Read-only."""
        with session_scope() as s:
            r = cashflow.receivables(data.invoices(s), data.today(), overdue_days)
        top = sorted(r.by_customer.items(), key=lambda kv: -kv[1])[:3]
        head = (f"{len(r.lines)} unpaid invoice(s) worth {inr(r.total_outstanding)}; overdue {inr(r.total_overdue)}. "
                + ("Biggest: " + ", ".join(f"{c} {inr(v)}" for c, v in top) if top else ""))
        return ReceivablesAnswer(headline=head, receivables=r, sources=r.sources)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def cash_forecast(days: int = 30) -> CashAnswer:
        """Projected bank balance for the next `days` days from open invoices, approved purchase
        orders and monthly overheads, and the first date cash would go negative. Read-only."""
        with session_scope() as s:
            f = cash_for(s, days)
        weekly = [WeekPoint(week_ending=d.day.isoformat(), balance=d.balance) for i, d in enumerate(f.days) if i % 7 == 6 or i == len(f.days) - 1]
        head = (f"Cash {inr(f.opening_balance)} today -> {inr(f.closing_balance)} in {days} days; lowest {inr(f.lowest_balance)} on {f.lowest_on}. "
                + (f"Cash goes NEGATIVE on {f.first_shortfall_date}." if f.first_shortfall_date else "No shortfall expected."))
        return CashAnswer(headline=head, opening_balance=f.opening_balance, closing_balance=f.closing_balance,
                          lowest_balance=f.lowest_balance, lowest_on=f.lowest_on.isoformat(),
                          first_shortfall_date=f.first_shortfall_date.isoformat() if f.first_shortfall_date else None,
                          weekly=weekly, assumptions=f.assumptions, sources=f.sources)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def list_pending_actions() -> PendingAnswer:
        """Drafts waiting for a human decision. Approval codes are never shown here."""
        with session_scope() as s:
            rows = s.query(Action).filter_by(business_id=business_id(), status="pending").all()
            acts = [PendingAction(action_id=a.id, type=a.type, summary=a.summary, requested_by_role=a.requested_by_role,
                                  created_at=a.created_at.isoformat()) for a in rows]
        return PendingAnswer(headline=f"{len(acts)} action(s) waiting for approval.", actions=acts)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def get_audit_log(limit: int = 15) -> AuditAnswer:
        """The most recent tool calls: who (role) did what, and the result."""
        with session_scope() as s:
            rows = s.query(AuditLog).filter_by(business_id=business_id()).order_by(AuditLog.id.desc()).limit(limit).all()
            out = [AuditRow(id=r.id, at=r.created_at.isoformat(timespec="seconds"), tool=r.tool, role=r.role, ok=r.ok,
                            result=r.result_summary[:200]) for r in rows]
        return AuditAnswer(headline=f"Last {len(out)} audited call(s).", rows=out)
