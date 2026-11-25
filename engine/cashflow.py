"""Receivables ageing and a simple daily cash forecast."""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel

from engine.types import InvoiceIn, OverheadIn, PurchaseOrderIn


class ReceivableLine(BaseModel):
    invoice_id: int
    number: str | None = None
    customer: str
    outstanding: float
    due_on: date
    days_overdue: int
    bucket: str


class ReceivablesResult(BaseModel):
    lines: list[ReceivableLine]
    total_outstanding: float
    total_overdue: float
    by_customer: dict[str, float]
    sources: list[str]


def _bucket(days: int) -> str:
    if days <= 0:
        return "not due"
    if days <= 30:
        return "1-30"
    if days <= 60:
        return "31-60"
    return "60+"


def receivables(invoices: list[InvoiceIn], today: date, overdue_days: int = 0) -> ReceivablesResult:
    """Open invoices with something left to collect, filtered to those at least `overdue_days` late.
    overdue_days=0 returns every unpaid invoice (due or not)."""
    lines: list[ReceivableLine] = []
    sources: list[str] = []
    for inv in invoices:
        if inv.status in ("cancelled", "draft") or inv.outstanding <= 0:
            continue
        late = (today - inv.due_on).days
        if overdue_days > 0 and late < overdue_days:
            continue
        lines.append(
            ReceivableLine(
                invoice_id=inv.id,
                number=inv.number,
                customer=inv.customer,
                outstanding=inv.outstanding,
                due_on=inv.due_on,
                days_overdue=max(0, late),
                bucket=_bucket(late),
            )
        )
        sources.append(inv.source)
    lines.sort(key=lambda l: -l.days_overdue)
    by_customer: dict[str, float] = {}
    for l in lines:
        by_customer[l.customer] = round(by_customer.get(l.customer, 0) + l.outstanding, 2)
    return ReceivablesResult(
        lines=lines,
        total_outstanding=round(sum(l.outstanding for l in lines), 2),
        total_overdue=round(sum(l.outstanding for l in lines if l.days_overdue > 0), 2),
        by_customer=by_customer,
        sources=sources,
    )


class DayBalance(BaseModel):
    day: date
    inflow: float
    outflow: float
    balance: float


class ForecastResult(BaseModel):
    opening_balance: float
    days: list[DayBalance]
    closing_balance: float
    lowest_balance: float
    lowest_on: date
    first_shortfall_date: date | None
    assumptions: list[str]
    sources: list[str]


def forecast(
    invoices: list[InvoiceIn],
    purchase_orders: list[PurchaseOrderIn],
    overheads: list[OverheadIn],
    days: int,
    opening_balance: float,
    today: date,
    opening_source: str = "opening balance",
    overdue_collection_lag_days: int = 15,
) -> ForecastResult:
    """Project the bank balance day by day.

    Inflows: each unpaid invoice is collected on its due date; already-overdue invoices are
    assumed to come in `overdue_collection_lag_days` from today.
    Outflows: approved/sent purchase orders are paid on their due date; monthly overheads are
    spread evenly per day (monthly / 30).
    """
    inflow: dict[date, float] = {}
    outflow: dict[date, float] = {}
    sources = [opening_source]
    for inv in invoices:
        if inv.status in ("cancelled", "draft") or inv.outstanding <= 0:
            continue
        d = inv.due_on if inv.due_on >= today else today + timedelta(days=overdue_collection_lag_days)
        inflow[d] = inflow.get(d, 0) + inv.outstanding
        sources.append(inv.source)
    for po in purchase_orders:
        # Committed POs are paid on their due date; ones already past due are assumed settled.
        if po.status not in ("approved", "sent", "partially_received", "received") or po.due_on is None or po.due_on < today:
            continue
        d = po.due_on
        outflow[d] = outflow.get(d, 0) + po.total
        sources.append(po.source)
    daily_overhead = sum(o.monthly_amount for o in overheads) / 30
    sources += [o.source for o in overheads]

    bal = opening_balance
    rows: list[DayBalance] = []
    first_short: date | None = None
    lowest, lowest_on = opening_balance, today
    for i in range(days):
        d = today + timedelta(days=i)
        inn = inflow.get(d, 0.0)
        out = outflow.get(d, 0.0) + daily_overhead
        bal += inn - out
        rows.append(DayBalance(day=d, inflow=round(inn, 2), outflow=round(out, 2), balance=round(bal, 2)))
        if bal < lowest:
            lowest, lowest_on = bal, d
        if bal < 0 and first_short is None:
            first_short = d
    return ForecastResult(
        opening_balance=opening_balance,
        days=rows,
        closing_balance=round(bal, 2),
        lowest_balance=round(lowest, 2),
        lowest_on=lowest_on,
        first_shortfall_date=first_short,
        assumptions=[
            f"Overdue invoices are assumed collected {overdue_collection_lag_days} days from today",
            "Invoices not yet due are assumed paid on their due date",
            "Monthly overheads are spread evenly at 1/30 per day",
            "Purchase orders placed are paid on their due date (past-due ones assumed already paid)",
        ],
        sources=list(dict.fromkeys(sources)),
    )
