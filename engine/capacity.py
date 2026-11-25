"""Capacity check: hours needed per stage vs hours available before the due date."""
from __future__ import annotations

import math

from pydantic import BaseModel

from engine.types import MachineIn, OrderIn, Params, RoutingStep, WorkCalendar, pnum


class StageLoad(BaseModel):
    stage: str
    machine_type: str
    hours_new_order: float
    hours_open_orders: float
    hours_needed: float
    hours_available: float  # with the shifts currently run
    hours_per_shift_window: float  # one shift across the whole window
    utilisation_pct: float
    shifts_needed: int
    current_shifts: int


class CapacityResult(BaseModel):
    production_qty: float
    working_days: int
    stages: list[StageLoad]
    bottleneck_stage: str | None
    extra_shifts_needed: int
    max_shifts_per_day: int
    feasible: bool
    notes: list[str]
    sources: list[str]


def production_qty(qty: float, rejection_pct: float) -> float:
    """Units to start so that `qty` good units come out after rejection."""
    r = rejection_pct / 100
    return qty / (1 - r) if r < 1 else qty


class MachineLoad(BaseModel):
    machine_type: str
    stages: list[str]
    hours_needed: float
    hours_available: float  # with current shifts
    utilisation_pct: float
    machines: int
    shifts_per_day: int


class LoadResult(BaseModel):
    working_days: int
    loads: list[MachineLoad]
    bottleneck: str | None
    peak_utilisation_pct: float
    sources: list[str]


def load(
    orders: list[OrderIn],
    routing: list[RoutingStep],
    machines: list[MachineIn],
    calendar: WorkCalendar,
    params: Params,
) -> LoadResult:
    """Floor load: hours each machine type needs for `orders` vs hours available in the window."""
    rejection, _ = pnum(params, "rejection_rate_pct", 0.0)
    days = calendar.working_days()
    loads: list[MachineLoad] = []
    for m in machines:
        need = 0.0
        stages: list[str] = []
        for o in orders:
            qty = production_qty(o.qty, rejection)
            for s in routing:
                if s.product_id == o.product_id and s.machine_type == m.machine_type:
                    need += qty * s.minutes_per_unit / 60
                    if s.stage not in stages:
                        stages.append(s.stage)
        avail = m.count * m.hours_per_shift * m.efficiency_pct / 100 * days * m.shifts_per_day
        loads.append(
            MachineLoad(
                machine_type=m.machine_type,
                stages=stages,
                hours_needed=round(need, 1),
                hours_available=round(avail, 1),
                utilisation_pct=round(need / avail * 100, 1) if avail else 0.0,
                machines=m.count,
                shifts_per_day=m.shifts_per_day,
            )
        )
    peak = max(loads, key=lambda l: l.utilisation_pct, default=None)
    return LoadResult(
        working_days=days,
        loads=loads,
        bottleneck=peak.machine_type if peak and peak.hours_needed else None,
        peak_utilisation_pct=peak.utilisation_pct if peak else 0.0,
        sources=[m.source for m in machines] + [r.source for r in routing],
    )


def check_order(
    order: OrderIn,
    routing: list[RoutingStep],
    machines: list[MachineIn],
    open_orders: list[OrderIn],
    calendar: WorkCalendar,
    params: Params,
) -> CapacityResult:
    sources: list[str] = []
    rejection, r_src = pnum(params, "rejection_rate_pct", 0.0)
    max_shifts, m_src = pnum(params, "max_shifts_per_day", 2)
    max_shifts = int(max_shifts)
    sources += [s for s in (r_src, m_src) if s]

    days = calendar.working_days()
    sources.append(
        f"calendar: {calendar.start.isoformat()} to {calendar.end.isoformat()}, "
        f"{days} working days (weekly off {calendar.weekly_off})"
    )
    by_type = {m.machine_type: m for m in machines}
    notes: list[str] = []

    # Open orders due on or before this order's due date compete for the same window.
    competing = [o for o in open_orders if o.due_date <= order.due_date]
    if competing:
        notes.append(
            f"{len(competing)} open order(s) due on or before {order.due_date.isoformat()} "
            "share the same capacity"
        )

    def hours_for(o: OrderIn, machine_type: str) -> float:
        qty = production_qty(o.qty, rejection)
        mins = sum(
            s.minutes_per_unit
            for s in routing
            if s.product_id == o.product_id and s.machine_type == machine_type
        )
        return qty * mins / 60

    stages: list[StageLoad] = []
    feasible = True
    worst_util, bottleneck, extra = -1.0, None, 0
    for step in (s for s in routing if s.product_id == order.product_id):
        sources.append(step.source)
        m = by_type.get(step.machine_type)
        new_h = hours_for(order, step.machine_type)
        open_h = sum(hours_for(o, step.machine_type) for o in competing)
        need = new_h + open_h
        if m is None:
            feasible = False
            notes.append(f"No machines of type '{step.machine_type}' for stage {step.stage}")
            continue
        sources.append(m.source)
        per_shift = m.count * m.hours_per_shift * m.efficiency_pct / 100 * days
        avail = per_shift * m.shifts_per_day
        shifts_needed = math.ceil(need / per_shift - 1e-9) if per_shift else 999
        util = need / avail * 100 if avail else float("inf")
        if shifts_needed > max_shifts:
            feasible = False
        stage_extra = max(0, shifts_needed - m.shifts_per_day)
        extra = max(extra, stage_extra)
        if util > worst_util:
            worst_util, bottleneck = util, step.stage
        stages.append(
            StageLoad(
                stage=step.stage,
                machine_type=step.machine_type,
                hours_new_order=round(new_h, 1),
                hours_open_orders=round(open_h, 1),
                hours_needed=round(need, 1),
                hours_available=round(avail, 1),
                hours_per_shift_window=round(per_shift, 1),
                utilisation_pct=round(util, 1),
                shifts_needed=shifts_needed,
                current_shifts=m.shifts_per_day,
            )
        )

    if days == 0:
        feasible = False
        notes.append("No working days before the due date")
    if not feasible and days:
        notes.append(
            f"Even at {max_shifts} shifts/day some stage cannot finish; consider job work "
            "(outsourcing) or a later due date"
        )

    return CapacityResult(
        production_qty=round(production_qty(order.qty, rejection), 1),
        working_days=days,
        stages=stages,
        bottleneck_stage=bottleneck,
        extra_shifts_needed=extra,
        max_shifts_per_day=max_shifts,
        feasible=feasible,
        notes=notes,
        sources=list(dict.fromkeys(sources)),
    )
