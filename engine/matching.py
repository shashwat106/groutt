"""Three-way purchase matching: what we ORDERED (PO) vs what ARRIVED (goods receipt) vs what
we were BILLED (supplier bill). The classic boring accountant job, done in code."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from engine.fmt import inr


class POLineIn(BaseModel):
    po_id: int
    supplier: str
    material: str
    unit: str
    qty: float
    rate: float
    status: str
    expected_on: date | None = None


class Movement(BaseModel):
    """A goods receipt line or a supplier bill line."""

    voucher: str
    date: date
    supplier: str
    material: str
    qty: float
    rate: float = 0
    amount: float = 0
    ref: str | None = None


class MatchRow(BaseModel):
    key: str
    po_id: int | None
    supplier: str
    material: str
    unit: str
    ordered_qty: float
    ordered_rate: float | None
    received_qty: float
    billed_qty: float
    billed_rate: float | None
    billed_amount: float
    payable_as_per_po: float
    difference: float  # billed - what the PO says we owe for what arrived; + means overbilled
    status: str  # matched | exception | awaiting_delivery | awaiting_bill | late | bill_without_po
    flags: list[str] = Field(default_factory=list)
    bills: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    rows: list[MatchRow]
    exceptions: list[MatchRow]
    matched_count: int
    overbilled_total: float
    headline: str


def three_way(
    po_lines: list[POLineIn],
    receipts: list[Movement],
    bills: list[Movement],
    today: date,
    qty_tolerance_pct: float = 2.0,
    rate_tolerance_pct: float = 0.5,
) -> MatchResult:
    rows: list[MatchRow] = []
    rec_left = [r.model_copy() for r in receipts]
    bill_left = [b.model_copy() for b in bills]

    def take(pool: list[Movement], line: POLineIn, limit: float) -> list[Movement]:
        """Assign movements to a PO line: explicit PO-n references first, then same supplier+material."""
        got: list[Movement] = []
        cands = [m for m in pool if m.supplier.lower() == line.supplier.lower() and m.material.lower() == line.material.lower()]
        cands.sort(key=lambda m: (0 if (m.ref or "").upper() == f"PO-{line.po_id}" else 1, m.date))
        total = 0.0
        for m in cands:
            ref_other = (m.ref or "").upper().startswith("PO-") and (m.ref or "").upper() != f"PO-{line.po_id}"
            if ref_other or total >= limit * (1 - qty_tolerance_pct / 100):
                continue
            got.append(m)
            pool.remove(m)
            total += m.qty
        return got

    for line in sorted(po_lines, key=lambda l: l.po_id):
        if line.status in ("draft", "cancelled"):
            continue
        u = line.unit.replace("_", " ")
        got_r = take(rec_left, line, line.qty * 1.5)
        got_b = take(bill_left, line, max(line.qty, sum(r.qty for r in got_r)) * 1.5)
        recv = sum(r.qty for r in got_r)
        bqty = sum(b.qty for b in got_b)
        bamt = sum(b.amount for b in got_b)
        brate = round(bamt / bqty, 2) if bqty else None
        payable = recv * line.rate
        flags: list[str] = []
        if bqty:
            if brate is not None and abs(brate - line.rate) > line.rate * rate_tolerance_pct / 100:
                flags.append(f"rate {inr(brate, False)} billed vs {inr(line.rate, False)} on PO")
            if recv and abs(bqty - recv) > recv * qty_tolerance_pct / 100:
                flags.append(f"billed {bqty:g} {u} but received {recv:g}")
            if recv and abs(recv - line.qty) > line.qty * qty_tolerance_pct / 100:
                flags.append(f"received {recv:g} {u} vs {line.qty:g} ordered")
            if not recv:
                flags.append("billed but nothing received yet")
            status = "exception" if flags else "matched"
        elif recv:
            status = "awaiting_bill"
            if abs(recv - line.qty) > line.qty * qty_tolerance_pct / 100:
                flags.append(f"received {recv:g} {u} vs {line.qty:g} ordered")
        else:
            status = "late" if line.expected_on and line.expected_on < today else "awaiting_delivery"
            if status == "late":
                flags.append(f"was due {line.expected_on:%d %b}, nothing received")
        rows.append(MatchRow(
            key=f"PO-{line.po_id}:{line.material}", po_id=line.po_id, supplier=line.supplier, material=line.material,
            unit=line.unit, ordered_qty=line.qty, ordered_rate=line.rate, received_qty=round(recv, 2), billed_qty=round(bqty, 2),
            billed_rate=brate, billed_amount=round(bamt, 2), payable_as_per_po=round(payable, 2),
            difference=round(bamt - payable, 2) if bqty else 0.0, status=status, flags=flags, bills=[b.voucher for b in got_b],
        ))

    for b in bill_left:  # bills nobody ordered
        rows.append(MatchRow(
            key=f"{b.voucher}:{b.material}", po_id=None, supplier=b.supplier, material=b.material, unit="",
            ordered_qty=0, ordered_rate=None, received_qty=0, billed_qty=b.qty, billed_rate=b.rate or None,
            billed_amount=b.amount, payable_as_per_po=0, difference=b.amount, status="bill_without_po",
            flags=["no purchase order for this bill"], bills=[b.voucher],
        ))

    exceptions = [r for r in rows if r.status in ("exception", "bill_without_po", "late")]
    over = round(sum(r.difference for r in exceptions if r.difference > 0), 2)
    matched = sum(1 for r in rows if r.status == "matched")
    if exceptions:
        headline = (f"{len(exceptions)} purchase problem(s) need checking"
                    + (f"; suppliers billed {inr(over)} more than agreed" if over else "") + f". {matched} matched cleanly.")
    else:
        headline = f"All clear: {matched} purchase(s) matched order, delivery and bill."
    return MatchResult(rows=rows, exceptions=exceptions, matched_count=matched, overbilled_total=over, headline=headline)
