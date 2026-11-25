"""Demo business: Sharma Footwear, Agra. A believable small leather footwear unit.

Tuned so that, about 2-3 weeks before the due date:
  check_order_feasibility("Oxford Classic", 2000, <10th of next month>)
  -> feasible with 1 extra shift (stitching is the bottleneck),
     leather short by about 400 sq ft, estimated profit about Rs 1.8L.
The verdict holds for roughly 14-26 working days of lead time.

Run:  uv run python -m seed.sharma_footwear
All dates are relative to today (or INDUSTRYOS_TODAY if set), so the demo never goes stale.
"""
from __future__ import annotations

from datetime import date, timedelta

from db.models import (
    BomItem,
    Business,
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
    Voucher,
)
from db.session import business_id, init_db, session_scope
from tools.data import today as get_today
from tools.onboard import apply_pack_defaults, set_profile


def demo_due_date(t: date) -> date:
    """The 10th of next month."""
    y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    return date(y, m, 10)


def seed(t: date | None = None, reset: bool = True) -> dict:
    t = t or get_today()
    init_db(drop=reset)
    bid = business_id()
    src = f"seed {t.isoformat()}"
    with session_scope() as s:
        s.add(Business(id=bid, name="Sharma Footwear", industry="leather_footwear", city="Agra", owner_phone="+91 98370 00011"))
        s.flush()
        apply_pack_defaults(s, "leather_footwear")
        # The owner has confirmed a few facts; the rest stay on pack defaults (shown as assumptions).
        set_profile(s, "opening_cash_balance", 420000, "owner_confirmed", 1.0)
        set_profile(s, "opening_cash_as_of", t.isoformat(), "owner_confirmed", 1.0)
        set_profile(s, "monthly_output_units", 8000, "owner_confirmed", 1.0)
        set_profile(s, "weekly_off_day", 6, "owner_confirmed", 1.0)

        sup = {
            "leather": Supplier(business_id=bid, name="Agra Leather Traders", phone="+91 98370 11223",
                                email="orders@agraleather.example", city="Agra", payment_terms_days=30, lead_time_days=4, source=src),
            "soles": Supplier(business_id=bid, name="Kanpur Sole Works", phone="+91 94150 33445",
                              email="sales@kanpursole.example", city="Kanpur", payment_terms_days=30, lead_time_days=6, source=src),
            "comp": Supplier(business_id=bid, name="Delhi Footwear Components", phone="+91 98110 55667",
                             email="info@dfcomponents.example", city="Delhi", payment_terms_days=15, lead_time_days=5, source=src),
        }
        s.add_all(sup.values())
        s.flush()

        def mat(name, unit, on_hand, reorder, cost, supplier):
            m = Material(business_id=bid, name=name, unit=unit, on_hand=on_hand, reorder_level=reorder,
                         unit_cost=cost, preferred_supplier_id=sup[supplier].id, source=src)
            s.add(m)
            return m

        leather = mat("Leather crust - tan", "sq_ft", 5440, 1500, 120, "leather")
        lining = mat("Lining leather", "sq_ft", 6000, 1500, 45, "leather")
        pu_sole = mat("PU sole - Oxford", "pair", 3000, 800, 220, "soles")
        rubber_sole = mat("Rubber sole - Derby", "pair", 500, 300, 150, "soles")
        insole = mat("Insole board", "pair", 4000, 1000, 30, "comp")
        laces = mat("Laces", "pair", 5000, 1000, 12, "comp")
        kit = mat("Thread & adhesive kit", "pair", 4500, 1000, 35, "comp")
        box = mat("Shoe box & packing", "piece", 3800, 1000, 40, "comp")
        s.flush()

        oxford = Product(business_id=bid, name="Oxford Classic", unit="pair", selling_price=1105, product_type="oxford", source=src)
        derby = Product(business_id=bid, name="Derby Comfort", unit="pair", selling_price=980, product_type="derby", source=src)
        s.add_all([oxford, derby])
        s.flush()

        for prod, lines in (
            (oxford, [(leather, 2.2, None), (lining, 1.8, 5), (pu_sole, 1, 0), (insole, 1, 0), (laces, 1, 0), (kit, 1, 0), (box, 1, 0)]),
            (derby, [(leather, 2.0, None), (lining, 1.6, 5), (rubber_sole, 1, 0), (insole, 1, 0), (laces, 1, 0), (kit, 1, 0), (box, 1, 0)]),
        ):
            for m, q, w in lines:
                s.add(BomItem(business_id=bid, product_id=prod.id, material_id=m.id, qty_per_unit=q, wastage_pct=w))

        stages = [("cutting", "clicking_press"), ("skiving", "skiving_machine"), ("stitching", "stitching_machine"),
                  ("lasting", "lasting_machine"), ("soling", "sole_press"), ("finishing", "finishing_station"),
                  ("packing", "packing_table")]
        minutes = {oxford.id: [12, 6, 45, 15, 10, 8, 4], derby.id: [10, 5, 40, 14, 10, 8, 4]}
        for pid, mins in minutes.items():
            for seq, ((stage, mt), mn) in enumerate(zip(stages, mins)):
                s.add(RoutingStepRow(business_id=bid, product_id=pid, seq=seq, stage=stage, machine_type=mt, minutes_per_unit=mn))

        for mt, count in [("clicking_press", 6), ("skiving_machine", 3), ("stitching_machine", 10), ("lasting_machine", 8),
                          ("sole_press", 5), ("finishing_station", 4), ("packing_table", 2)]:
            s.add(Machine(business_id=bid, machine_type=mt, count=count, hours_per_shift=8, shifts_per_day=1, efficiency_pct=85))

        for role, heads, rate in [("Cutters (clickers)", 6, 120), ("Stitchers", 20, 110), ("Lasting & soling", 12, 105),
                                  ("Finishing & packing", 8, 90), ("Helpers", 6, 80)]:
            s.add(Labour(business_id=bid, role=role, headcount=heads, cost_per_hour=rate))

        for name, amt in [("Factory rent", 120000), ("Power & diesel", 85000), ("Staff salaries (office, supervisors)", 240000),
                          ("Maintenance & misc", 55000)]:
            s.add(Overhead(business_id=bid, name=name, monthly_amount=amt))

        custs = {}
        for key, name, city, phone, email, terms in [
            ("raj", "Raj Shoe Mart", "Delhi", "+91 98100 12345", "accounts@rajshoemart.example", 45),
            ("mehra", "Mehra Footwear Distributors", "Jaipur", "+91 94140 23456", "mehra.fd@example.com", 30),
            ("kolkata", "Kolkata Shoe House", "Kolkata", "+91 98300 34567", "ksh@example.com", 45),
            ("pune", "Pune Style Retail", "Pune", "+91 98220 45678", "buying@punestyle.example", 30),
            ("euro", "EuroStride Imports (Hamburg)", "Hamburg", "+49 40 5550 1234", "purchase@eurostride.example", 60),
        ]:
            c = Customer(business_id=bid, name=name, city=city, phone=phone, email=email, payment_terms_days=terms, source=src)
            s.add(c)
            custs[key] = c
        s.flush()

        # Open order competing for capacity and leather before the demo due date.
        s.add(Order(business_id=bid, customer_id=custs["mehra"].id, product_id=derby.id, qty=400,
                    due_date=t + timedelta(days=12), status="in_production", price=950))
        # Past orders, already dispatched and invoiced.
        s.add(Order(business_id=bid, customer_id=custs["euro"].id, product_id=oxford.id, qty=600,
                    due_date=t - timedelta(days=30), status="invoiced", price=1067))

        def inv(no, c, amount, issued_ago, terms, paid=0.0, status="open"):
            issued = t - timedelta(days=issued_ago)
            s.add(Invoice(business_id=bid, number=no, customer_id=custs[c].id, amount=amount,
                          gst_amount=round(amount * 5 / 105, 2), issued_on=issued, due_on=issued + timedelta(days=terms),
                          paid_amount=paid, status=status, source=src))

        inv("INV-0098", "pune", 85000, 70, 30, paid=85000, status="paid")
        inv("INV-0099", "raj", 120000, 114, 45)          # ~69 days late
        inv("INV-0101", "raj", 210000, 74, 45)           # ~29 days late
        inv("INV-0102", "mehra", 145000, 64, 30, paid=45000)  # ~34 days late, part paid
        inv("INV-0103", "kolkata", 320000, 49, 45)       # ~4 days late
        inv("INV-0104", "pune", 98000, 22, 30)           # due in ~8 days
        inv("INV-0105", "euro", 640000, 29, 60)          # due in ~31 days

        # Soles already on order (sent), payable within the week: squeezes cash.
        s.add(PurchaseOrder(business_id=bid, supplier_id=sup["soles"].id, total=330000, status="sent",
                            items_json=[{"material_id": pu_sole.id, "material": pu_sole.name, "qty": 1500, "unit": "pair",
                                         "rate": 220, "amount": 330000}],
                            expected_on=t + timedelta(days=3), due_on=t + timedelta(days=6)))
        # Older POs, already delivered (stock above already includes them).
        s.add(PurchaseOrder(business_id=bid, supplier_id=sup["comp"].id, total=80000, status="received",
                            items_json=[{"material_id": box.id, "material": box.name, "qty": 2000, "unit": "piece",
                                         "rate": 40, "amount": 80000, "received": 2000}],
                            expected_on=t - timedelta(days=3), due_on=t - timedelta(days=2)))
        s.add(PurchaseOrder(business_id=bid, supplier_id=sup["leather"].id, total=45000, status="received",
                            items_json=[{"material_id": lining.id, "material": lining.name, "qty": 1000, "unit": "sq_ft",
                                         "rate": 45, "amount": 45000, "received": 1000}],
                            expected_on=t - timedelta(days=1), due_on=t + timedelta(days=40)))
        inv("INV-0108", "pune", 147000, 1, 30)

        # The last few days of the books, as synced from Tally (effects already reflected above).
        def v(vtype, ago, number, party, amount, items=(), gst=0.0, ref=None, effect=""):
            s.add(Voucher(business_id=bid, vtype=vtype, number=number, date=t - timedelta(days=ago), party=party, amount=amount,
                          gst_amount=gst, items_json=[dict(zip(("material", "qty", "unit", "rate", "amount"), i)) for i in items],
                          ref=ref, effect=effect, external_key=f"{vtype}:{number}:{(t - timedelta(days=ago)):%Y%m%d}:{party}",
                          source=f"tally sync (history) {src}"))

        v("receipt_note", 3, "DFC-GRN-311", "Delhi Footwear Components", 80000, [("Shoe box & packing", 2000, "piece", 40, 80000)],
          effect="stock in: +2000 piece Shoe box & packing against PO-2 (received)")
        v("purchase", 3, "DFC/2026/884", "Delhi Footwear Components", 88200, [("Shoe box & packing", 2000, "piece", 42, 84000)],
          gst=4200, effect="supplier bill DFC/2026/884 recorded for checking")
        v("receipt", 2, "RCPT-0448", "Mehra Footwear Distributors", 45000, ref="INV-0102", effect="payment applied to INV-0102")
        v("payment", 2, "PMT-0095", "Delhi Footwear Components", 88200, effect="payment of Rs 88,200 to Delhi Footwear Components recorded")
        v("sales", 1, "INV-0108", "Pune Style Retail", 147000, [("Derby Comfort", 150, "pair", 933.33, 140000)], gst=7000,
          effect="new invoice INV-0108 for Pune Style Retail")
        v("receipt_note", 1, "ALT-GRN-77", "Agra Leather Traders", 45000, [("Lining leather", 1000, "sq_ft", 45, 45000)],
          effect="stock in: +1000 sq_ft Lining leather against PO-3 (received)")
        v("purchase", 1, "ALT/1123", "Agra Leather Traders", 47250, [("Lining leather", 1000, "sq_ft", 45, 45000)], gst=2250,
          effect="supplier bill ALT/1123 recorded for checking")

    if reset:
        from connectors.tally_sync import inbox_dir
        INBOX = inbox_dir()
        for f in list(INBOX.glob("*.xml")) + list((INBOX / "processed").glob("*.xml")):
            f.unlink()
    from seed.daybook import write_todays_daybook
    daybook = write_todays_daybook(t)
    return {"business": "Sharma Footwear", "today": t.isoformat(), "demo_due_date": demo_due_date(t).isoformat(),
            "todays_daybook": str(daybook)}


if __name__ == "__main__":
    info = seed()
    print(f"Seeded {info['business']} (today = {info['today']}).")
    print(f'Demo: check_order_feasibility("Oxford Classic", 2000, "{info["demo_due_date"]}")')
    print(f"Today's Tally Day Book for the live sync demo: {info['todays_daybook']}")
