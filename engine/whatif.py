"""Order evaluation (cost + capacity + stock + profit) and what-if comparisons.

`evaluate_order` is the single place where an order's verdict is computed. Both the
`check_order_feasibility` tool and `what_if` use it, so the numbers always agree.
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field

from engine import capacity as cap
from engine import costing, stock
from engine.fmt import inr, qty
from engine.types import (
    BomLine,
    LabourIn,
    MachineIn,
    MaterialIn,
    OrderIn,
    OverheadIn,
    Param,
    Params,
    ProductIn,
    RoutingStep,
    WorkCalendar,
    pnum,
)


class Scenario(BaseModel):
    """Everything the engine needs to judge one order. Built by the tool layer from the DB."""

    product: ProductIn
    order: OrderIn
    bom: list[BomLine]
    materials: dict[int, MaterialIn]
    routing: list[RoutingStep]
    labour: list[LabourIn]
    overheads: list[OverheadIn]
    machines: list[MachineIn]
    open_orders: list[OrderIn]
    calendar: WorkCalendar
    params: Params
    incoming: dict[int, float] = Field(default_factory=dict)
    supplier_lead_days: dict[int, int] = Field(default_factory=dict)  # supplier_id -> days
    supplier_names: dict[int, str] = Field(default_factory=dict)
    today: date


class MaterialArrival(BaseModel):
    material: str
    supplier: str | None
    lead_time_days: int | None
    expected_arrival: date | None
    needed_by: date
    in_time: bool | None


class OrderEvaluation(BaseModel):
    verdict: str  # feasible | feasible_with_extra_shift | feasible_with_purchase | feasible_with_extra_shift_and_purchase | not_feasible
    headline: str
    qty: float
    price_per_unit: float
    revenue: float
    unit_cost: float
    production_cost: float
    extra_shift_cost: float
    estimated_profit: float
    profit_pct: float
    cost: costing.CostBreakdown
    capacity: cap.CapacityResult
    stock: stock.ShortfallResult
    arrivals: list[MaterialArrival]
    next_actions: list[str]
    assumptions: list[str]
    sources: list[str]


def evaluate_order(s: Scenario) -> OrderEvaluation:
    cost = costing.unit_cost(s.product, s.bom, s.materials, s.routing, s.labour, s.overheads, s.params)
    capres = cap.check_order(s.order, s.routing, s.machines, s.open_orders, s.calendar, s.params)
    short = stock.shortfall(s.order, s.bom, s.materials, s.open_orders, s.params, s.incoming)

    price = s.order.price if s.order.price is not None else s.product.selling_price
    revenue = s.order.qty * price
    production_cost = s.order.qty * cost.total
    shift_cost_day, sc_src = pnum(s.params, "extra_shift_cost_per_day", 0.0)
    # Shifts added by a what-if are already in machines.shifts_per_day, but still cost money.
    forced, _ = pnum(s.params, "forced_extra_shifts", 0)
    extra_shift_cost = (capres.extra_shifts_needed + forced) * capres.working_days * shift_cost_day
    profit = revenue - production_cost - extra_shift_cost

    buffer_days, b_src = pnum(s.params, "material_buffer_days", 7)
    needed_by = s.order.due_date - timedelta(days=int(buffer_days))
    arrivals: list[MaterialArrival] = []
    for line in short.short:
        sid = line.preferred_supplier_id
        lead = s.supplier_lead_days.get(sid) if sid is not None else None
        eta = s.today + timedelta(days=lead) if lead is not None else None
        arrivals.append(
            MaterialArrival(
                material=line.material,
                supplier=s.supplier_names.get(sid) if sid is not None else None,
                lead_time_days=lead,
                expected_arrival=eta,
                needed_by=needed_by,
                in_time=(eta <= needed_by) if eta else None,
            )
        )

    late_material = any(a.in_time is False for a in arrivals)
    feasible = capres.feasible and not late_material
    extra = capres.extra_shifts_needed
    if not feasible:
        verdict = "not_feasible"
    elif extra and short.short:
        verdict = "feasible_with_extra_shift_and_purchase"
    elif extra:
        verdict = "feasible_with_extra_shift"
    elif short.short:
        verdict = "feasible_with_purchase"
    else:
        verdict = "feasible"

    parts: list[str] = []
    if verdict == "not_feasible":
        parts.append("Not feasible by the due date")
    elif extra:
        parts.append(f"Feasible with {extra} extra shift{'s' if extra > 1 else ''} (bottleneck: {capres.bottleneck_stage})")
    else:
        parts.append("Feasible on current shifts")
    for l in short.short:
        parts.append(f"{l.material} short by {qty(l.shortfall_qty, l.unit)} ({inr(l.shortfall_cost)})")
    parts.append(f"Estimated profit {inr(profit)} ({profit / revenue * 100:.1f}% of {inr(revenue)})" if revenue else "")
    headline = ". ".join(p for p in parts if p) + "."

    actions: list[str] = []
    if extra and capres.feasible:
        actions.append(
            f"Plan {extra} extra shift(s) on {capres.bottleneck_stage} for {capres.working_days} working days "
            f"(about {inr(extra_shift_cost)}), or send part of the work out as job work"
        )
    for a, l in zip(arrivals, short.short):
        who = a.supplier or "a supplier"
        when = f", arrives {a.expected_arrival.isoformat()}" if a.expected_arrival else ""
        actions.append(f"Draft a purchase order for {qty(l.shortfall_qty, l.unit)} of {l.material} from {who}{when}")
        if a.in_time is False:
            actions.append(f"{l.material} would arrive after {a.needed_by.isoformat()}: find a faster supplier or move the due date")
    if not capres.feasible:
        actions.append("Ask the customer for a later due date or split delivery")
    if verdict != "not_feasible":
        actions.append("Send the customer a quotation at the agreed price")

    # Which inputs are still generic pack defaults (not the business's own numbers)?
    assumptions = [
        f"{k} = {p.value} ({p.source})" for k, p in s.params.items() if "pack default" in p.source
    ]

    sources = cost.sources + capres.sources + short.sources
    if sc_src:
        sources.append(sc_src)
    if b_src:
        sources.append(b_src)
    sources.append(f"order price: {'quoted ' + inr(price) if s.order.price is not None else 'product selling price'}")

    return OrderEvaluation(
        verdict=verdict,
        headline=headline,
        qty=s.order.qty,
        price_per_unit=price,
        revenue=round(revenue, 2),
        unit_cost=cost.total,
        production_cost=round(production_cost, 2),
        extra_shift_cost=round(extra_shift_cost, 2),
        estimated_profit=round(profit, 2),
        profit_pct=round(profit / revenue * 100, 1) if revenue else 0.0,
        cost=cost,
        capacity=capres,
        stock=short,
        arrivals=arrivals,
        next_actions=actions,
        assumptions=assumptions,
        sources=list(dict.fromkeys(sources)),
    )


class Changes(BaseModel):
    """What-if levers. All optional; percentages are relative changes, points are absolute."""

    price_pct: float = Field(0, description="Change selling price by this % (e.g. 5 = +5%)")
    material_cost_pct: float = Field(0, description="Change all material costs by this %")
    wastage_pct_points: float = Field(0, description="Add this many points to leather/cutting wastage %")
    rejection_pct_points: float = Field(0, description="Add this many points to rejection rate %")
    extra_shifts: int = Field(0, description="Run this many more shifts per day on every machine")
    efficiency_pct_points: float = Field(0, description="Add this many points to machine efficiency %")
    qty_pct: float = Field(0, description="Change the order quantity by this %")


class Snapshot(BaseModel):
    verdict: str
    unit_cost: float
    price_per_unit: float
    estimated_profit: float
    profit_pct: float
    extra_shifts_needed: int
    bottleneck_stage: str | None
    total_shortfall_cost: float


class WhatIfResult(BaseModel):
    changes: Changes
    before: Snapshot
    after: Snapshot
    profit_change: float
    summary: str
    sources: list[str]


def _snap(e: OrderEvaluation) -> Snapshot:
    return Snapshot(
        verdict=e.verdict,
        unit_cost=e.unit_cost,
        price_per_unit=e.price_per_unit,
        estimated_profit=e.estimated_profit,
        profit_pct=e.profit_pct,
        extra_shifts_needed=e.capacity.extra_shifts_needed,
        bottleneck_stage=e.capacity.bottleneck_stage,
        total_shortfall_cost=e.stock.total_shortfall_cost,
    )


def _changed(s: Scenario, c: Changes) -> Scenario:
    n = s.model_copy(deep=True)
    base_price = n.order.price if n.order.price is not None else n.product.selling_price
    n.order.price = base_price * (1 + c.price_pct / 100)
    n.order.qty = n.order.qty * (1 + c.qty_pct / 100)
    for m in n.materials.values():
        m.unit_cost = m.unit_cost * (1 + c.material_cost_pct / 100)
    if c.wastage_pct_points:
        for b in n.bom:
            if b.wastage_pct:  # only lines that actually have wastage; never below zero
                b.wastage_pct = max(0.0, b.wastage_pct + c.wastage_pct_points)
        w, src = pnum(n.params, "cutting_wastage_pct", 0.0)
        n.params["cutting_wastage_pct"] = Param(value=max(0.0, w + c.wastage_pct_points), source=f"what-if (was {src})")
    if c.rejection_pct_points:
        r, src = pnum(n.params, "rejection_rate_pct", 0.0)
        n.params["rejection_rate_pct"] = Param(value=r + c.rejection_pct_points, source=f"what-if (was {src})")
    if c.extra_shifts:
        f, _ = pnum(n.params, "forced_extra_shifts", 0)
        n.params["forced_extra_shifts"] = Param(value=f + c.extra_shifts, source="what-if: extra shifts")
    for m in n.machines:
        m.shifts_per_day += c.extra_shifts
        m.efficiency_pct = min(100.0, m.efficiency_pct + c.efficiency_pct_points)
    return n


def apply(scenario: Scenario, changes: Changes) -> WhatIfResult:
    before = evaluate_order(scenario)
    after = evaluate_order(_changed(scenario, changes))
    b, a = _snap(before), _snap(after)
    delta = a.estimated_profit - b.estimated_profit
    summary = (
        f"Profit {inr(b.estimated_profit)} -> {inr(a.estimated_profit)} ({'+' if delta >= 0 else ''}{inr(delta)}); "
        f"unit cost {inr(b.unit_cost, short=False)} -> {inr(a.unit_cost, short=False)}; "
        f"verdict {b.verdict} -> {a.verdict}"
    )
    return WhatIfResult(
        changes=changes,
        before=b,
        after=a,
        profit_change=round(delta, 2),
        summary=summary,
        sources=before.sources,
    )
