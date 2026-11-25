"""Stock maths: material needed for an order, what open orders already reserve, shortfall."""
from __future__ import annotations

from pydantic import BaseModel

from engine.capacity import production_qty
from engine.types import BomLine, MaterialIn, OrderIn, Params, pnum


class ShortLine(BaseModel):
    material_id: int
    material: str
    unit: str
    required: float
    reserved_by_open_orders: float
    on_hand: float
    on_order: float = 0
    available: float
    shortfall_qty: float
    unit_cost: float
    shortfall_cost: float
    preferred_supplier_id: int | None


class ShortfallResult(BaseModel):
    lines: list[ShortLine]
    short: list[ShortLine]
    total_shortfall_cost: float
    sources: list[str]


class StockLine(BaseModel):
    material: str
    unit: str
    on_hand: float
    reserved_by_open_orders: float
    free: float
    reorder_level: float
    status: str  # ok | low | out
    suggested_reorder_qty: float
    value: float


class StockStatus(BaseModel):
    items: list[StockLine]
    total_value: float
    low_count: int
    sources: list[str]


def _material_need(
    o: OrderIn, bom: list[BomLine], material_id: int, rejection: float, default_wastage: float
) -> float:
    qty = production_qty(o.qty, rejection)
    total = 0.0
    for line in bom:
        if line.product_id == o.product_id and line.material_id == material_id:
            w = default_wastage if line.wastage_pct is None else line.wastage_pct
            total += qty * line.qty_per_unit * (1 + w / 100)
    return total


def shortfall(
    order: OrderIn,
    bom: list[BomLine],
    materials: dict[int, MaterialIn],
    open_orders: list[OrderIn],
    params: Params | None = None,
    incoming: dict[int, float] | None = None,
) -> ShortfallResult:
    """Like an MRP run: need = this order; available = on hand - reserved by open orders
    + incoming on approved/sent purchase orders (not yet received)."""
    params = params or {}
    incoming = incoming or {}
    rejection, r_src = pnum(params, "rejection_rate_pct", 0.0)
    default_wastage, w_src = pnum(params, "cutting_wastage_pct", 0.0)
    sources = [s for s in (r_src, w_src) if s]

    lines: list[ShortLine] = []
    for mid in dict.fromkeys(b.material_id for b in bom if b.product_id == order.product_id):
        mat = materials[mid]
        need = _material_need(order, bom, mid, rejection, default_wastage)
        reserved = sum(_material_need(o, bom, mid, rejection, default_wastage) for o in open_orders)
        on_order = incoming.get(mid, 0.0)
        available = mat.on_hand - reserved + on_order
        short = max(0.0, need - max(available, 0.0))
        lines.append(
            ShortLine(
                material_id=mid,
                material=mat.name,
                unit=mat.unit,
                required=round(need, 1),
                reserved_by_open_orders=round(reserved, 1),
                on_hand=mat.on_hand,
                on_order=round(on_order, 1),
                available=round(available, 1),
                shortfall_qty=round(short, 1),
                unit_cost=mat.unit_cost,
                shortfall_cost=round(short * mat.unit_cost, 2),
                preferred_supplier_id=mat.preferred_supplier_id,
            )
        )
        sources.append(mat.source)
    sources += [b.source for b in bom if b.product_id == order.product_id]
    short_lines = [l for l in lines if l.shortfall_qty > 0]
    return ShortfallResult(
        lines=lines,
        short=short_lines,
        total_shortfall_cost=round(sum(l.shortfall_cost for l in short_lines), 2),
        sources=list(dict.fromkeys(sources)),
    )


def status(
    materials: dict[int, MaterialIn],
    bom: list[BomLine],
    open_orders: list[OrderIn],
    params: Params | None = None,
) -> StockStatus:
    params = params or {}
    rejection, _ = pnum(params, "rejection_rate_pct", 0.0)
    default_wastage, _ = pnum(params, "cutting_wastage_pct", 0.0)
    items: list[StockLine] = []
    for mid, mat in materials.items():
        reserved = sum(_material_need(o, bom, mid, rejection, default_wastage) for o in open_orders)
        free = mat.on_hand - reserved
        if free <= 0:
            st = "out"
        elif free < mat.reorder_level:
            st = "low"
        else:
            st = "ok"
        # Reorder up to twice the reorder level, as a simple rule of thumb.
        suggest = max(0.0, 2 * mat.reorder_level - free) if st != "ok" else 0.0
        items.append(
            StockLine(
                material=mat.name,
                unit=mat.unit,
                on_hand=mat.on_hand,
                reserved_by_open_orders=round(reserved, 1),
                free=round(free, 1),
                reorder_level=mat.reorder_level,
                status=st,
                suggested_reorder_qty=round(suggest, 1),
                value=round(mat.on_hand * mat.unit_cost, 2),
            )
        )
    return StockStatus(
        items=items,
        total_value=round(sum(i.value for i in items), 2),
        low_count=sum(1 for i in items if i.status != "ok"),
        sources=[m.source for m in materials.values()],
    )
