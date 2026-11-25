"""Plain input/output models for the engine.

The engine never touches the database. The tool layer loads rows, wraps them in
these models (each record carries a human-readable `source`), and the engine
returns results that list every source it used.
"""
from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field


class Param(BaseModel):
    value: float | str
    source: str


Params = dict[str, Param]


def pnum(params: Params, key: str, default: float) -> tuple[float, str | None]:
    """Read a numeric param. Returns (value, source or None if the default was used)."""
    p = params.get(key)
    if p is None:
        return default, None
    return float(p.value), p.source


class ProductIn(BaseModel):
    id: int
    name: str
    unit: str = "pair"
    selling_price: float
    source: str


class MaterialIn(BaseModel):
    id: int
    name: str
    unit: str
    on_hand: float
    unit_cost: float
    reorder_level: float = 0
    preferred_supplier_id: int | None = None
    source: str


class BomLine(BaseModel):
    product_id: int
    material_id: int
    qty_per_unit: float
    wastage_pct: float | None = None  # None -> use the pack's default wastage
    source: str


class RoutingStep(BaseModel):
    product_id: int
    stage: str
    machine_type: str
    minutes_per_unit: float
    source: str


class MachineIn(BaseModel):
    machine_type: str
    count: int
    hours_per_shift: float
    shifts_per_day: int
    efficiency_pct: float
    source: str


class LabourIn(BaseModel):
    role: str
    headcount: int
    cost_per_hour: float
    source: str


class OverheadIn(BaseModel):
    name: str
    monthly_amount: float
    source: str


class OrderIn(BaseModel):
    product_id: int
    qty: float
    due_date: date
    price: float | None = None
    label: str = "new order"


class WorkCalendar(BaseModel):
    start: date
    end: date
    weekly_off: list[int] = Field(default_factory=lambda: [6])  # 0=Mon ... 6=Sun
    holidays: list[date] = Field(default_factory=list)

    def working_days(self) -> int:
        days, d = 0, self.start
        while d <= self.end:
            if d.weekday() not in self.weekly_off and d not in self.holidays:
                days += 1
            d += timedelta(days=1)
        return days


class InvoiceIn(BaseModel):
    id: int
    customer: str
    amount: float  # total incl. GST
    paid_amount: float = 0
    issued_on: date
    due_on: date
    status: str = "open"
    number: str | None = None
    source: str

    @property
    def outstanding(self) -> float:
        return round(self.amount - self.paid_amount, 2)


class PurchaseOrderIn(BaseModel):
    id: int
    supplier: str
    total: float
    status: str
    due_on: date | None
    source: str
