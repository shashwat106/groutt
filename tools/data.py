"""DB -> engine inputs. Every record gets a human-readable `source` so answers can cite it."""
import os
from datetime import date

from fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session

from db.models import (
    BomItem,
    Business,
    BusinessProfile,
    Customer,
    Invoice,
    Labour,
    Machine,
    Material,
    Order,
    Overhead,
    Product,
    PurchaseOrder,
    RoutingStepRow,
    Supplier,
)
from db.session import business_id
from engine.types import (
    BomLine,
    InvoiceIn,
    LabourIn,
    MachineIn,
    MaterialIn,
    OrderIn,
    OverheadIn,
    Param,
    Params,
    ProductIn,
    PurchaseOrderIn,
    RoutingStep,
    WorkCalendar,
)
from engine.whatif import Scenario

OPEN_ORDER_STATUSES = ("open", "in_production")


def today() -> date:
    """INDUSTRYOS_TODAY pins the date for demos and tests."""
    t = os.environ.get("INDUSTRYOS_TODAY")
    return date.fromisoformat(t) if t else date.today()


def business(sess: Session) -> Business:
    b = sess.get(Business, business_id())
    if b is None:
        raise ToolError("No business set up yet. Run the seed or call select_industry first.")
    return b


def profile_source_label(row: BusinessProfile) -> str:
    src = row.source
    if src.startswith("pack_default:"):
        return f"{src.split(':', 1)[1]} pack default: {row.key}"
    if src == "owner_confirmed":
        return f"owner confirmed: {row.key} ({row.updated_at:%Y-%m-%d})" if row.updated_at else f"owner confirmed: {row.key}"
    return f"{src}: {row.key}"


def profile_rows(sess: Session) -> dict[str, BusinessProfile]:
    return {r.key: r for r in sess.query(BusinessProfile).filter_by(business_id=business_id())}


def params(sess: Session) -> Params:
    out: Params = {}
    for key, row in profile_rows(sess).items():
        v = row.value_json
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            out[key] = Param(value=v, source=profile_source_label(row))
    return out


def find_one(sess: Session, model, name: str, what: str):
    rows = sess.query(model).filter_by(business_id=business_id()).all()
    exact = [r for r in rows if r.name.lower() == name.lower()]
    if exact:
        return exact[0]
    partial = [r for r in rows if name.lower() in r.name.lower()]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(r.name for r in rows)
    if not partial:
        raise ToolError(f"No {what} called '{name}'. Known {what}s: {names}")
    raise ToolError(f"'{name}' matches several {what}s: {', '.join(r.name for r in partial)}")


def product_in(p: Product) -> ProductIn:
    return ProductIn(id=p.id, name=p.name, unit=p.unit, selling_price=p.selling_price,
                     source=f"products.{p.name}.selling_price ({p.source})")


def materials(sess: Session) -> dict[int, MaterialIn]:
    return {
        m.id: MaterialIn(
            id=m.id, name=m.name, unit=m.unit, on_hand=m.on_hand, unit_cost=m.unit_cost,
            reorder_level=m.reorder_level, preferred_supplier_id=m.preferred_supplier_id,
            source=f"materials.{m.name} on_hand/unit_cost ({m.source})",
        )
        for m in sess.query(Material).filter_by(business_id=business_id())
    }


def bom(sess: Session) -> list[BomLine]:
    mats = {m.id: m.name for m in sess.query(Material).filter_by(business_id=business_id())}
    prods = {p.id: p.name for p in sess.query(Product).filter_by(business_id=business_id())}
    return [
        BomLine(product_id=b.product_id, material_id=b.material_id, qty_per_unit=b.qty_per_unit,
                wastage_pct=b.wastage_pct, source=f"bom_items.{prods.get(b.product_id)}/{mats.get(b.material_id)}")
        for b in sess.query(BomItem).filter_by(business_id=business_id())
    ]


def routing(sess: Session) -> list[RoutingStep]:
    prods = {p.id: p.name for p in sess.query(Product).filter_by(business_id=business_id())}
    rows = sess.query(RoutingStepRow).filter_by(business_id=business_id()).order_by(RoutingStepRow.seq)
    return [
        RoutingStep(product_id=r.product_id, stage=r.stage, machine_type=r.machine_type,
                    minutes_per_unit=r.minutes_per_unit,
                    source=f"routing_steps.{prods.get(r.product_id)}/{r.stage} ({r.minutes_per_unit} min)")
        for r in rows
    ]


def machines(sess: Session) -> list[MachineIn]:
    return [
        MachineIn(machine_type=m.machine_type, count=m.count, hours_per_shift=m.hours_per_shift,
                  shifts_per_day=m.shifts_per_day, efficiency_pct=m.efficiency_pct,
                  source=f"machines.{m.machine_type} (x{m.count}, {m.shifts_per_day} shift, {m.efficiency_pct:g}% eff.)")
        for m in sess.query(Machine).filter_by(business_id=business_id())
    ]


def labour(sess: Session) -> list[LabourIn]:
    return [
        LabourIn(role=l.role, headcount=l.headcount, cost_per_hour=l.cost_per_hour,
                 source=f"labour.{l.role} ({l.headcount} @ Rs {l.cost_per_hour:g}/h)")
        for l in sess.query(Labour).filter_by(business_id=business_id())
    ]


def overheads(sess: Session) -> list[OverheadIn]:
    return [
        OverheadIn(name=o.name, monthly_amount=o.monthly_amount, source=f"overheads.{o.name}")
        for o in sess.query(Overhead).filter_by(business_id=business_id())
    ]


def open_orders(sess: Session) -> list[OrderIn]:
    rows = sess.query(Order).filter(Order.business_id == business_id(), Order.status.in_(OPEN_ORDER_STATUSES))
    return [OrderIn(product_id=o.product_id, qty=o.qty, due_date=o.due_date, price=o.price, label=f"order #{o.id}")
            for o in rows]


def incoming(sess: Session) -> dict[int, float]:
    """Material qty on approved/sent purchase orders that has not been received yet."""
    out: dict[int, float] = {}
    for po in sess.query(PurchaseOrder).filter(
        PurchaseOrder.business_id == business_id(), PurchaseOrder.status.in_(("approved", "sent", "partially_received"))
    ):
        for item in po.items_json:
            left = max(0.0, float(item["qty"]) - float(item.get("received", 0)))
            out[item["material_id"]] = out.get(item["material_id"], 0) + left
    return out


def vouchers(sess: Session):
    from db.models import Voucher
    from engine.brief import VoucherIn

    return [
        VoucherIn(vtype=v.vtype, number=v.number, date=v.date, party=v.party, amount=v.amount, gst_amount=v.gst_amount,
                  items=v.items_json or [], effect=v.effect, source=f"vouchers.{v.vtype} {v.number or ''} ({v.source})")
        for v in sess.query(Voucher).filter_by(business_id=business_id()).order_by(Voucher.date, Voucher.id)
    ]


def matching_inputs(sess: Session):
    """PO lines, goods receipts and supplier bills for three-way matching."""
    from db.models import Voucher
    from engine.matching import Movement, POLineIn

    sups = {s.id: s.name for s in sess.query(Supplier).filter_by(business_id=business_id())}
    lines = []
    for po in sess.query(PurchaseOrder).filter_by(business_id=business_id()):
        for it in po.items_json:
            lines.append(POLineIn(po_id=po.id, supplier=sups.get(po.supplier_id, "?"), material=it["material"], unit=it["unit"],
                                  qty=float(it["qty"]), rate=float(it["rate"]), status=po.status, expected_on=po.expected_on))
    rec, bills = [], []
    for v in sess.query(Voucher).filter(Voucher.business_id == business_id(), Voucher.vtype.in_(("receipt_note", "purchase"))):
        for it in v.items_json or []:
            mv = Movement(voucher=v.number or f"V{v.id}", date=v.date, supplier=v.party or "?", material=it["material"],
                          qty=float(it["qty"]), rate=float(it.get("rate") or 0), amount=float(it.get("amount") or 0), ref=v.ref)
            (rec if v.vtype == "receipt_note" else bills).append(mv)
    return lines, rec, bills


def cash_today(sess: Session) -> tuple[float, str]:
    """Owner-confirmed opening balance + money in - money out synced from the books since then."""
    from db.models import Voucher

    rows = profile_rows(sess)
    ob = rows.get("opening_cash_balance")
    if ob is None:
        return 0.0, "opening balance not set (0 assumed)"
    as_of_row = rows.get("opening_cash_as_of")
    as_of = date.fromisoformat(str(as_of_row.value_json)) if as_of_row else (ob.updated_at.date() if ob.updated_at else today())
    vs = sess.query(Voucher).filter(Voucher.business_id == business_id(), Voucher.date >= as_of,
                                    Voucher.vtype.in_(("receipt", "payment"))).all()
    moved = sum(v.amount if v.vtype == "receipt" else -v.amount for v in vs)
    src = profile_source_label(ob) + (f" + {len(vs)} synced receipt/payment voucher(s) since {as_of:%d %b}" if vs else "")
    return float(ob.value_json) + moved, src


def calendar(prm: Params, start: date, end: date) -> WorkCalendar:
    off = prm.get("weekly_off_day")
    return WorkCalendar(start=start, end=end, weekly_off=[int(float(off.value))] if off else [6])


def scenario(sess: Session, product: Product, qty: float, due: date, price: float | None) -> Scenario:
    prm = params(sess)
    t = today()
    if due <= t:
        raise ToolError(f"Due date {due} must be after today ({t}).")
    sups = sess.query(Supplier).filter_by(business_id=business_id()).all()
    return Scenario(
        product=product_in(product),
        order=OrderIn(product_id=product.id, qty=qty, due_date=due, price=price),
        bom=bom(sess),
        materials=materials(sess),
        routing=routing(sess),
        labour=labour(sess),
        overheads=overheads(sess),
        machines=machines(sess),
        open_orders=open_orders(sess),
        calendar=calendar(prm, t, due),
        params=prm,
        incoming=incoming(sess),
        supplier_lead_days={s.id: s.lead_time_days for s in sups},
        supplier_names={s.id: s.name for s in sups},
        today=t,
    )


def invoices(sess: Session) -> list[InvoiceIn]:
    custs = {c.id: c.name for c in sess.query(Customer).filter_by(business_id=business_id())}
    return [
        InvoiceIn(id=i.id, customer=custs.get(i.customer_id, "?"), amount=i.amount, paid_amount=i.paid_amount,
                  issued_on=i.issued_on, due_on=i.due_on, status=i.status, number=i.number,
                  source=f"invoices.{i.number or i.id} ({i.source})")
        for i in sess.query(Invoice).filter_by(business_id=business_id())
    ]


def purchase_orders(sess: Session) -> list[PurchaseOrderIn]:
    sups = {s.id: s.name for s in sess.query(Supplier).filter_by(business_id=business_id())}
    return [
        PurchaseOrderIn(id=p.id, supplier=sups.get(p.supplier_id, "?"), total=p.total, status=p.status,
                        due_on=p.due_on, source=f"purchase_orders.PO-{p.id} ({p.status})")
        for p in sess.query(PurchaseOrder).filter_by(business_id=business_id())
    ]
