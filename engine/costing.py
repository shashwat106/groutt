"""True cost per unit: material + wastage + labour + overhead + rejection allowance."""
from __future__ import annotations

from pydantic import BaseModel

from engine.types import (
    BomLine,
    LabourIn,
    MaterialIn,
    OverheadIn,
    Params,
    ProductIn,
    RoutingStep,
    pnum,
)


class CostLine(BaseModel):
    material: str
    qty: float
    unit: str
    unit_cost: float
    base_cost: float
    wastage_pct: float
    wastage_cost: float


class CostBreakdown(BaseModel):
    product: str
    material: float
    wastage: float
    labour: float
    overhead: float
    rejection_allowance: float
    total: float
    selling_price: float
    margin_per_unit: float
    margin_pct: float
    lines: list[CostLine]
    labour_minutes: float
    labour_rate_per_hour: float
    overhead_per_unit: float
    sources: list[str]


def weighted_labour_rate(labour: list[LabourIn]) -> float:
    heads = sum(l.headcount for l in labour)
    if heads == 0:
        return 0.0
    return sum(l.headcount * l.cost_per_hour for l in labour) / heads


def unit_cost(
    product: ProductIn,
    bom: list[BomLine],
    materials: dict[int, MaterialIn],
    routing: list[RoutingStep],
    labour: list[LabourIn],
    overheads: list[OverheadIn],
    params: Params,
) -> CostBreakdown:
    sources: list[str] = [product.source]
    default_wastage, w_src = pnum(params, "cutting_wastage_pct", 0.0)

    lines: list[CostLine] = []
    for line in (b for b in bom if b.product_id == product.id):
        mat = materials[line.material_id]
        if line.wastage_pct is None:
            wastage = default_wastage
            if w_src:
                sources.append(w_src)
        else:
            wastage = line.wastage_pct
        base = line.qty_per_unit * mat.unit_cost
        lines.append(
            CostLine(
                material=mat.name,
                qty=line.qty_per_unit,
                unit=mat.unit,
                unit_cost=mat.unit_cost,
                base_cost=round(base, 2),
                wastage_pct=wastage,
                wastage_cost=round(base * wastage / 100, 2),
            )
        )
        sources += [line.source, mat.source]

    material = sum(l.base_cost for l in lines)
    wastage_total = sum(l.wastage_cost for l in lines)

    steps = [r for r in routing if r.product_id == product.id]
    minutes = sum(s.minutes_per_unit for s in steps)
    rate = weighted_labour_rate(labour)
    labour_cost = minutes / 60 * rate
    sources += [s.source for s in steps] + [l.source for l in labour]

    monthly_overhead = sum(o.monthly_amount for o in overheads)
    monthly_output, mo_src = pnum(params, "monthly_output_units", 0.0)
    overhead_per_unit = monthly_overhead / monthly_output if monthly_output else 0.0
    sources += [o.source for o in overheads]
    if mo_src:
        sources.append(mo_src)

    subtotal = material + wastage_total + labour_cost + overhead_per_unit
    rejection_pct, r_src = pnum(params, "rejection_rate_pct", 0.0)
    r = rejection_pct / 100
    rejection_allowance = subtotal * r / (1 - r) if r < 1 else 0.0
    if r_src:
        sources.append(r_src)

    parts = [round(x, 2) for x in (material, wastage_total, labour_cost, overhead_per_unit, rejection_allowance)]
    total = round(sum(parts), 2)  # total is exactly the sum of the shown parts
    margin = product.selling_price - total
    return CostBreakdown(
        product=product.name,
        material=parts[0],
        wastage=parts[1],
        labour=parts[2],
        overhead=parts[3],
        rejection_allowance=parts[4],
        total=total,
        selling_price=product.selling_price,
        margin_per_unit=round(margin, 2),
        margin_pct=round(margin / product.selling_price * 100, 1) if product.selling_price else 0.0,
        lines=lines,
        labour_minutes=minutes,
        labour_rate_per_hour=round(rate, 2),
        overhead_per_unit=round(overhead_per_unit, 2),
        sources=_dedupe(sources),
    )


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
