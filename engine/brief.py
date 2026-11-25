"""'What happened' summaries and the owner's daily brief. Deterministic text from numbers;
the AI reads it out, it never writes the numbers itself."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from engine.fmt import inr, qty


class VoucherIn(BaseModel):
    vtype: str
    number: str | None
    date: date
    party: str | None
    amount: float
    gst_amount: float = 0
    items: list[dict] = Field(default_factory=list)
    effect: str | None = None
    source: str


class Happened(BaseModel):
    start: date
    end: date
    sales_total: float
    sales: list[VoucherIn]
    money_in_total: float
    money_in: list[VoucherIn]
    bills_total: float
    bills: list[VoucherIn]
    goods_in: list[VoucherIn]
    money_out_total: float
    money_out: list[VoucherIn]
    voucher_count: int
    headline: str
    sources: list[str]


def happened(vouchers: list[VoucherIn], start: date, end: date) -> Happened:
    vs = [v for v in vouchers if start <= v.date <= end]
    by = lambda t: [v for v in vs if v.vtype == t]  # noqa: E731
    sales, rin, bills, gin, rout = by("sales"), by("receipt"), by("purchase"), by("receipt_note"), by("payment")
    span = start.strftime("%d %b") if start == end else f"{start:%d %b}-{end:%d %b}"
    head = (f"{span}: sales {inr(sum(v.amount for v in sales))} ({len(sales)}), money in {inr(sum(v.amount for v in rin))}, "
            f"supplier bills {inr(sum(v.amount for v in bills))}, {len(gin)} delivery(ies) received, "
            f"money out {inr(sum(v.amount for v in rout))}.")
    return Happened(
        start=start, end=end,
        sales_total=round(sum(v.amount for v in sales), 2), sales=sales,
        money_in_total=round(sum(v.amount for v in rin), 2), money_in=rin,
        bills_total=round(sum(v.amount for v in bills), 2), bills=bills, goods_in=gin,
        money_out_total=round(sum(v.amount for v in rout), 2), money_out=rout,
        voucher_count=len(vs), headline=head, sources=list(dict.fromkeys(v.source for v in vs)),
    )


class BriefLine(BaseModel):
    kind: str  # sales | money_in | purchases | check | collect | cash | stock | approvals | orders
    tone: str  # good | info | warn | bad
    text: str
    link: str = "/"


class DailyBrief(BaseModel):
    for_date: date
    headline: str
    lines: list[BriefLine]
    whatsapp_text: str
    sources: list[str]


def daily_brief(
    day: date,
    h: Happened,
    overdue_total: float,
    top_overdue: list[tuple[str, float, int]],  # (customer, amount, days late)
    first_shortfall: date | None,
    lowest_balance: float,
    low_stock: list[tuple[str, float, str]],  # (material, free qty, unit)
    purchase_problems: list[str],
    pending_approvals: int,
    orders_due: list[tuple[str, float, date]],  # (label, qty, due)
    business: str,
    today_mode: bool = False,
) -> DailyBrief:
    L: list[BriefLine] = []
    when = "Today so far" if today_mode else day.strftime("%a %d %b")
    if h.sales:
        top = max(h.sales, key=lambda v: v.amount)
        L.append(BriefLine(kind="sales", tone="good", link="/orders",
                           text=f"Sales: {inr(h.sales_total)} from {len(h.sales)} invoice(s); biggest {top.party} {inr(top.amount)}."))
    else:
        L.append(BriefLine(kind="sales", tone="info", link="/orders", text="Sales: nothing invoiced."))
    if h.money_in:
        who = ", ".join(f"{v.party} {inr(v.amount)}" for v in h.money_in[:3])
        L.append(BriefLine(kind="money_in", tone="good", link="/money", text=f"Money in: {inr(h.money_in_total)} ({who})."))
    else:
        L.append(BriefLine(kind="money_in", tone="info", link="/money", text="Money in: no payments received."))
    if h.goods_in or h.bills:
        recv = "; ".join(
            f"{qty(i['qty'], i.get('unit') or '')} {i['material']} from {v.party}" for v in h.goods_in for i in v.items
        )
        txt = f"Purchases: received {recv}." if recv else "Purchases: nothing received."
        if h.bills:
            txt += f" Supplier bills booked {inr(h.bills_total)}."
        L.append(BriefLine(kind="purchases", tone="info", link="/purchases", text=txt))
    for p in purchase_problems[:2]:
        L.append(BriefLine(kind="check", tone="bad", link="/purchases", text=f"Check: {p}"))
    if overdue_total > 0:
        tops = ", ".join(f"{c} {inr(a)} ({d} days)" for c, a, d in top_overdue[:2])
        L.append(BriefLine(kind="collect", tone="warn", link="/money", text=f"Collect: {inr(overdue_total)} overdue; {tops}."))
    if first_shortfall:
        L.append(BriefLine(kind="cash", tone="bad", link="/money",
                           text=f"Cash: bank balance goes below zero on {first_shortfall:%d %b} unless overdue money comes in."))
    else:
        L.append(BriefLine(kind="cash", tone="good", link="/money", text=f"Cash: OK for 30 days (lowest {inr(lowest_balance)})."))
    if low_stock:
        L.append(BriefLine(kind="stock", tone="warn", link="/stock",
                           text="Stock: " + "; ".join(f"{m} low ({qty(f, u)} free)" for m, f, u in low_stock[:3]) + "."))
    if orders_due:
        L.append(BriefLine(kind="orders", tone="info", link="/orders",
                           text="Due soon: " + "; ".join(f"{lbl} {q:,.0f} pairs by {d:%d %b}" for lbl, q, d in orders_due[:2]) + "."))
    if pending_approvals:
        L.append(BriefLine(kind="approvals", tone="warn", link="/approvals",
                           text=f"Waiting for you: {pending_approvals} draft(s) to approve."))

    bad = sum(1 for l in L if l.tone == "bad")
    headline = (f"{when}: {inr(h.sales_total)} sold, {inr(h.money_in_total)} received"
                + (f", {bad} thing(s) need attention." if bad else ", nothing urgent."))
    whatsapp = f"*{business} - {when}*\n" + "\n".join(f"- {l.text}" for l in L)
    return DailyBrief(for_date=day, headline=headline, lines=L, whatsapp_text=whatsapp, sources=h.sources)
