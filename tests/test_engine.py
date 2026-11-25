"""Pure engine tests: no database."""
from datetime import date

import pytest

from engine import capacity, cashflow, costing, stock, whatif
from engine.fmt import inr
from engine.types import (
    BomLine,
    InvoiceIn,
    LabourIn,
    MachineIn,
    MaterialIn,
    OrderIn,
    OverheadIn,
    Param,
    ProductIn,
    PurchaseOrderIn,
    RoutingStep,
    WorkCalendar,
)

S = "test"
P = {
    "cutting_wastage_pct": Param(value=10, source="pack default: cutting_wastage_pct"),
    "rejection_rate_pct": Param(value=0, source="test"),
    "monthly_output_units": Param(value=1000, source="test"),
    "max_shifts_per_day": Param(value=2, source="test"),
    "extra_shift_cost_per_day": Param(value=1000, source="test"),
    "material_buffer_days": Param(value=2, source="test"),
}
PRODUCT = ProductIn(id=1, name="Shoe", selling_price=500, source=S)
MATS = {
    1: MaterialIn(id=1, name="Leather", unit="sq_ft", on_hand=100, unit_cost=100, preferred_supplier_id=9, source=S),
    2: MaterialIn(id=2, name="Sole", unit="pair", on_hand=1000, unit_cost=50, source=S),
}
BOM = [
    BomLine(product_id=1, material_id=1, qty_per_unit=2, wastage_pct=None, source=S),  # pack default 10%
    BomLine(product_id=1, material_id=2, qty_per_unit=1, wastage_pct=0, source=S),
]
ROUTING = [
    RoutingStep(product_id=1, stage="cutting", machine_type="press", minutes_per_unit=6, source=S),
    RoutingStep(product_id=1, stage="stitching", machine_type="stitch", minutes_per_unit=60, source=S),
]
LABOUR = [LabourIn(role="a", headcount=1, cost_per_hour=100, source=S), LabourIn(role="b", headcount=3, cost_per_hour=60, source=S)]
OVERHEADS = [OverheadIn(name="rent", monthly_amount=50000, source=S)]
MACHINES = [
    MachineIn(machine_type="press", count=1, hours_per_shift=8, shifts_per_day=1, efficiency_pct=100, source=S),
    MachineIn(machine_type="stitch", count=2, hours_per_shift=8, shifts_per_day=1, efficiency_pct=100, source=S),
]
# Mon 2 Nov 2026 .. Fri 6 Nov 2026 = 5 working days (Sunday off)
CAL = WorkCalendar(start=date(2026, 11, 2), end=date(2026, 11, 6), weekly_off=[6])


def test_costing_breakdown_sums():
    c = costing.unit_cost(PRODUCT, BOM, MATS, ROUTING, LABOUR, OVERHEADS, P)
    assert c.material == 250  # 2*100 + 1*50
    assert c.wastage == 20  # 10% of 200 leather, from the pack default
    assert c.labour_rate_per_hour == 70  # (100 + 3*60) / 4
    assert c.labour == pytest.approx(77.0)  # 66 min at 70/h
    assert c.overhead == 50  # 50000 / 1000
    assert c.total == pytest.approx(c.material + c.wastage + c.labour + c.overhead + c.rejection_allowance)
    assert c.total == pytest.approx(397.0)
    assert any("pack default: cutting_wastage_pct" in s for s in c.sources)


def test_costing_rejection_allowance():
    p = dict(P, rejection_rate_pct=Param(value=20, source="t"))
    c = costing.unit_cost(PRODUCT, BOM, MATS, ROUTING, LABOUR, OVERHEADS, p)
    assert c.rejection_allowance == pytest.approx(397 * 0.25)  # r/(1-r) uplift
    assert c.total == pytest.approx(397 / 0.8)


def test_calendar_working_days():
    assert CAL.working_days() == 5
    assert WorkCalendar(start=date(2026, 11, 2), end=date(2026, 11, 8)).working_days() == 6


def test_capacity_bottleneck_and_extra_shift():
    # stitching: 120 units * 60 min = 120h; 1 shift window = 2 machines*8h*5d = 80h -> 2 shifts
    order = OrderIn(product_id=1, qty=120, due_date=date(2026, 11, 6))
    r = capacity.check_order(order, ROUTING, MACHINES, [], CAL, P)
    assert r.working_days == 5
    assert r.bottleneck_stage == "stitching"
    assert r.extra_shifts_needed == 1
    assert r.feasible
    st = {s.stage: s for s in r.stages}
    assert st["stitching"].hours_needed == 120 and st["stitching"].hours_available == 80
    assert st["cutting"].shifts_needed == 1  # 12h of 40h


def test_capacity_open_orders_compete_and_infeasible():
    order = OrderIn(product_id=1, qty=120, due_date=date(2026, 11, 6))
    open_ = [OrderIn(product_id=1, qty=60, due_date=date(2026, 11, 5))]  # +60h stitching -> 180h > 160h
    r = capacity.check_order(order, ROUTING, MACHINES, open_, CAL, P)
    assert not r.feasible
    later = [OrderIn(product_id=1, qty=60, due_date=date(2026, 12, 30))]  # due later: not competing
    assert capacity.check_order(order, ROUTING, MACHINES, later, CAL, P).feasible


def test_shortfall_maths():
    order = OrderIn(product_id=1, qty=100, due_date=date(2026, 11, 6))
    open_ = [OrderIn(product_id=1, qty=10, due_date=date(2026, 11, 5))]
    r = stock.shortfall(order, BOM, MATS, open_, P)
    leather = next(l for l in r.lines if l.material == "Leather")
    assert leather.required == pytest.approx(220)  # 100 * 2 * 1.10
    assert leather.reserved_by_open_orders == pytest.approx(22)
    assert leather.shortfall_qty == pytest.approx(142)  # 220 - (100 - 22)
    assert leather.shortfall_cost == pytest.approx(14200)
    assert [l.material for l in r.short] == ["Leather"]
    # incoming PO covers part of it
    r2 = stock.shortfall(order, BOM, MATS, open_, P, incoming={1: 100})
    assert r2.short[0].shortfall_qty == pytest.approx(42)


def test_receivables_ageing():
    t = date(2026, 11, 23)
    inv = [
        InvoiceIn(id=1, customer="A", amount=1000, issued_on=date(2026, 9, 1), due_on=date(2026, 10, 1), source=S),
        InvoiceIn(id=2, customer="A", amount=500, paid_amount=200, issued_on=date(2026, 11, 1), due_on=date(2026, 11, 20), source=S),
        InvoiceIn(id=3, customer="B", amount=700, issued_on=date(2026, 11, 20), due_on=date(2026, 12, 20), source=S),
        InvoiceIn(id=4, customer="B", amount=900, paid_amount=900, issued_on=date(2026, 9, 1), due_on=date(2026, 10, 1), source=S),
    ]
    r = cashflow.receivables(inv, t)
    assert r.total_outstanding == 2000  # 1000 + 300 + 700
    assert r.total_overdue == 1300
    assert r.lines[0].days_overdue == 53 and r.lines[0].bucket == "31-60"
    assert cashflow.receivables(inv, t, overdue_days=30).total_outstanding == 1000


def test_cash_forecast_shortfall_date():
    t = date(2026, 11, 23)
    inv = [InvoiceIn(id=1, customer="A", amount=5000, issued_on=t, due_on=date(2026, 11, 30), source=S)]
    pos = [PurchaseOrderIn(id=1, supplier="X", total=4000, status="sent", due_on=date(2026, 11, 25), source=S)]
    oh = [OverheadIn(name="rent", monthly_amount=3000, source=S)]  # 100/day
    f = cashflow.forecast(inv, pos, oh, days=10, opening_balance=4000, today=t)
    # day0 3900, day1 3800, day2 (25th) 3700-4000 = -300 -> first shortfall
    assert f.first_shortfall_date == date(2026, 11, 25)
    assert f.days[2].balance == pytest.approx(-300)
    assert f.days[7].balance == pytest.approx(-800 + 5000)  # invoice collected on the 30th
    draft = [PurchaseOrderIn(id=2, supplier="X", total=4000, status="draft", due_on=date(2026, 11, 25), source=S)]
    assert cashflow.forecast(inv, draft, oh, 10, 4000, t).first_shortfall_date is None


def _scenario():
    return whatif.Scenario(
        product=PRODUCT, order=OrderIn(product_id=1, qty=120, due_date=date(2026, 11, 6)), bom=BOM, materials=MATS,
        routing=ROUTING, labour=LABOUR, overheads=OVERHEADS, machines=MACHINES, open_orders=[], calendar=CAL, params=P,
        supplier_lead_days={9: 1}, supplier_names={9: "Leather Co"}, today=date(2026, 11, 2),
    )


def test_evaluate_order_verdict_and_profit():
    e = whatif.evaluate_order(_scenario())
    assert e.verdict == "feasible_with_extra_shift_and_purchase"
    assert e.revenue == 60000
    assert e.extra_shift_cost == 5000  # 1 shift * 5 days * 1000
    assert e.estimated_profit == pytest.approx(60000 - 120 * 397 - 5000)
    assert e.arrivals[0].in_time is True
    assert any("cutting_wastage_pct" in a for a in e.assumptions)


def test_what_if_before_after():
    r = whatif.apply(_scenario(), whatif.Changes(price_pct=10))
    assert r.after.price_per_unit == pytest.approx(550)
    assert r.profit_change == pytest.approx(120 * 50)
    r2 = whatif.apply(_scenario(), whatif.Changes(extra_shifts=1))
    assert r2.after.extra_shifts_needed == 0
    assert r2.after.estimated_profit == pytest.approx(r2.before.estimated_profit)  # shift still costs money
    r3 = whatif.apply(_scenario(), whatif.Changes(wastage_pct_points=-10))
    assert r3.after.unit_cost == pytest.approx(377)


def test_inr_format():
    assert inr(180560) == "₹1.81L"
    assert inr(47608) == "₹47,608"
    assert inr(1234567, short=False) == "₹12,34,567"
    assert inr(25000000) == "₹2.50Cr"
