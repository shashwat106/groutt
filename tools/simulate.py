"""Simulate tools (read-only): judge an order before saying yes, and what-if levers."""
from datetime import date

from fastmcp import FastMCP
from pydantic import BaseModel

from control.audit import audited
from control.roles import SIMULATE, requires
from db.session import session_scope
from engine.whatif import Changes, OrderEvaluation, WhatIfResult, apply, evaluate_order
from tools import data

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


class Shortfall(BaseModel):
    material: str
    short_qty: float
    unit: str
    cost: float
    supplier: str | None
    arrives: date | None
    in_time: bool | None


class OrderCheck(BaseModel):
    """Compact verdict: everything needed to answer, without the full working."""

    verdict: str
    headline: str
    estimated_profit: float
    profit_pct: float
    revenue: float
    unit_cost: float
    extra_shifts_needed: int
    extra_shift_cost: float
    bottleneck_stage: str | None
    working_days: int
    shortfalls: list[Shortfall]
    next_actions: list[str]
    still_on_industry_defaults: list[str]
    key_sources: list[str]
    sources_total: int
    full: OrderEvaluation | None = None


def evaluate(product: str, qty: float, due_date: date, price: float | None = None) -> OrderEvaluation:
    with session_scope() as s:
        p = data.find_one(s, data.Product, product, "product")
        return evaluate_order(data.scenario(s, p, qty, due_date, price))


def compact(e: OrderEvaluation, detail: bool) -> OrderCheck:
    shorts = [
        Shortfall(material=l.material, short_qty=l.shortfall_qty, unit=l.unit, cost=l.shortfall_cost,
                  supplier=a.supplier, arrives=a.expected_arrival, in_time=a.in_time)
        for l, a in zip(e.stock.short, e.arrivals)
    ]
    key = [s for s in e.sources if s.startswith(("products.", "materials.", "machines.stitching", "order price"))][:6]
    return OrderCheck(
        verdict=e.verdict, headline=e.headline, estimated_profit=e.estimated_profit, profit_pct=e.profit_pct,
        revenue=e.revenue, unit_cost=e.unit_cost, extra_shifts_needed=e.capacity.extra_shifts_needed,
        extra_shift_cost=e.extra_shift_cost, bottleneck_stage=e.capacity.bottleneck_stage,
        working_days=e.capacity.working_days, shortfalls=shorts, next_actions=e.next_actions,
        still_on_industry_defaults=[a.split(" (")[0] for a in e.assumptions], key_sources=key,
        sources_total=len(e.sources), full=e if detail else None,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(SIMULATE)
    async def check_order_feasibility(product: str, qty: float, due_date: date, price: float | None = None,
                                      detail: bool = False) -> OrderCheck:
        """Can we take this order? Checks capacity (bottleneck, extra shifts), material shortfall
        (after open orders and incoming POs, with supplier arrival dates) and profit. Returns a
        verdict, a headline to read out, next actions and which inputs are still industry defaults.
        `price` = quoted price per unit (default: selling price). Set detail=true only when the user
        asks for the full breakdown (every stage, material and source). Read-only."""
        return compact(evaluate(product, qty, due_date, price), detail)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(SIMULATE)
    async def what_if(product: str, qty: float, due_date: date, changes: Changes, price: float | None = None) -> WhatIfResult:
        """Compare an order before vs after changes: price, material cost, cutting yield, extra
        shift, machine efficiency. E.g. {"price_pct": 5} or {"extra_shifts": 1}. Read-only."""
        with session_scope() as s:
            p = data.find_one(s, data.Product, product, "product")
            return apply(data.scenario(s, p, qty, due_date, price), changes)
