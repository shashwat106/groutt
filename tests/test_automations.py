"""Tally sync, three-way matching, the daily brief and the automations."""
import shutil
from datetime import date, datetime

import pytest
from fastmcp import Client

from db.models import Action, AutomationRun, Brief, Invoice, Material, Outbox, PurchaseOrder, Voucher
from db.session import session_scope
from engine.matching import Movement, POLineIn, three_way
from importers import tally


def drop_daybook(db):
    from connectors.tally_sync import inbox_dir
    inbox_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy(db["todays_daybook"], inbox_dir())


def approve_leather_po():
    from control import approvals
    from tools.act import create_po_draft
    with session_scope() as s:
        m = s.query(Material).filter_by(name="Leather crust - tan").one()
        d = create_po_draft(s, [(m, 396.7, None)], None, "owner")
    with session_scope() as s:
        approvals.execute(s, s.get(Action, d["action_id"]), "owner")
    return d["po_id"]


def test_tally_voucher_parsing(db):
    vs = tally.read_vouchers(db["todays_daybook"])
    kinds = sorted(v["vtype"] for v in vs)
    assert kinds == ["payment", "purchase", "purchase", "receipt", "receipt_note", "receipt_note", "sales"]
    bill = next(v for v in vs if v["number"] == "ALT/1131")
    assert bill["items"][0] == {"material": "Leather crust - tan", "qty": 410.0, "unit": "sq_ft", "rate": 125.0, "amount": 51250.0}
    assert bill["gst_amount"] == 2562.5 and bill["amount"] == 53812.5
    rcpt = next(v for v in vs if v["vtype"] == "receipt")
    assert rcpt["ref"] == "INV-0099" and rcpt["amount"] == 120000
    assert next(v for v in vs if v["number"] == "KSW-GRN-19")["ref"] == "PO-1"


def test_sync_applies_effects_and_is_idempotent(db):
    from automations import jobs
    leather_po = approve_leather_po()
    drop_daybook(db)
    r = jobs.run("sync", "manual")
    assert r.ok and "7 new voucher(s)" in r.summary
    with session_scope() as s:
        assert s.query(Invoice).filter_by(number="INV-0099").one().status == "paid"
        assert s.query(Invoice).filter_by(number="INV-0109").one().amount == 210000
        assert s.query(Material).filter_by(name="Leather crust - tan").one().on_hand == 5440 + 410
        assert s.get(PurchaseOrder, leather_po).status == "received"
        assert s.get(PurchaseOrder, 1).status == "received"  # soles, referenced as PO-1
    drop_daybook(db)  # same file again
    jobs.run("sync", "manual")
    with session_scope() as s:
        assert s.query(Voucher).filter(Voucher.date == date(2026, 11, 23)).count() == 7
        assert s.query(Material).filter_by(name="Leather crust - tan").one().on_hand == 5850


def test_three_way_matching_engine():
    t = date(2026, 11, 23)
    lines = [POLineIn(po_id=1, supplier="A", material="Leather", unit="sq_ft", qty=400, rate=120, status="received"),
             POLineIn(po_id=2, supplier="B", material="Sole", unit="pair", qty=100, rate=200, status="received"),
             POLineIn(po_id=3, supplier="B", material="Box", unit="piece", qty=50, rate=10, status="sent", expected_on=date(2026, 11, 20))]
    rec = [Movement(voucher="G1", date=t, supplier="A", material="Leather", qty=410),
           Movement(voucher="G2", date=t, supplier="B", material="Sole", qty=100)]
    bills = [Movement(voucher="B1", date=t, supplier="A", material="Leather", qty=410, rate=125, amount=51250),
             Movement(voucher="B2", date=t, supplier="B", material="Sole", qty=100, rate=200, amount=20000),
             Movement(voucher="B3", date=t, supplier="C", material="Glue", qty=5, rate=100, amount=500)]
    r = three_way(lines, rec, bills, t)
    by = {x.key: x for x in r.rows}
    leather = by["PO-1:Leather"]
    assert leather.status == "exception" and leather.difference == pytest.approx(51250 - 410 * 120)
    assert any("rate" in f for f in leather.flags) and any("410" in f for f in leather.flags)
    assert by["PO-2:Sole"].status == "matched"
    assert by["PO-3:Box"].status == "late"
    assert by["B3:Glue"].status == "bill_without_po"
    assert r.matched_count == 1 and r.overbilled_total == pytest.approx(2050 + 500)


def test_match_job_drafts_supplier_query_once(db):
    from automations import jobs
    jobs.run("match", "manual")
    jobs.run("match", "manual")
    with session_scope() as s:
        qs = s.query(Action).filter_by(type="supplier_query").all()
    assert len(qs) == 1 and qs[0].status == "pending" and qs[0].requested_by_role == "automation"
    assert "Delhi Footwear Components" in qs[0].summary and "₹4,000" in qs[0].summary


def test_brief_before_and_after_sync(db):
    from automations import jobs
    with session_scope() as s:
        before = jobs.build_brief(s)
    assert any("goes below zero" in l.text for l in before.lines)
    yesterday = date(2026, 11, 22)
    with session_scope() as s:
        y = jobs.build_brief(s, yesterday)
    assert "Pune Style Retail" in " ".join(l.text for l in y.lines)
    approve_leather_po()
    drop_daybook(db)
    jobs.run("sync", "manual")
    with session_scope() as s:
        after = jobs.build_brief(s)
    text = " ".join(l.text for l in after.lines)
    assert "Lucknow Footwear Bazaar" in text and "Raj Shoe Mart ₹1.20L" in text
    assert "Cash: OK" in text  # Raj's payment fixed the dip
    assert "₹2,050 extra" in text  # leather overbilled


def test_brief_job_sends_to_owner(db):
    from automations import jobs
    r = jobs.run("brief", "schedule")
    assert r.ok
    with session_scope() as s:
        b = s.query(Brief).one()
        assert b.for_date == date(2026, 11, 22)  # the morning brief covers yesterday
        out = s.query(Outbox).filter_by(channel="whatsapp").one()
        assert out.recipient == "+91 98370 00011" and "Sharma Footwear" in out.body


def test_chase_job_drafts_once_per_customer(db):
    from automations import jobs
    r = jobs.run("chase", "manual")
    assert "Raj Shoe Mart" in r.summary and "Mehra" in r.summary and "Kolkata" not in r.summary
    jobs.run("chase", "manual")
    with session_scope() as s:
        assert s.query(Action).filter_by(type="payment_reminder").count() == 2


def test_scheduler_due_logic(db, monkeypatch):
    from datetime import timedelta

    from automations import jobs
    # Runs are stamped with the real clock, so simulate times on today's real date.
    real_today = datetime.now().date()
    early = datetime.combine(real_today, datetime.min.time()).replace(hour=8).astimezone()
    assert jobs.next_due("sync", None, early) <= early
    assert jobs.next_due("brief", None, early).hour == 9  # not yet 9:00
    monkeypatch.setitem(jobs.JOBS["brief"], "daily_at", "00:00")  # make the daily jobs due whatever time tests run
    monkeypatch.setitem(jobs.JOBS["chase"], "daily_at", "00:00")
    now = datetime.now().astimezone()
    ran = jobs.run_due(now)
    assert set(ran) == {"sync", "match", "brief", "chase"}
    assert jobs.run_due(now + timedelta(seconds=5)) == []  # nothing due again immediately
    assert "sync" in jobs.run_due(now + timedelta(seconds=120))  # sync is due again after its interval
    with session_scope() as s:
        assert s.query(AutomationRun).count() == 5  # 4 jobs + sync again after its interval


async def test_mcp_brief_and_bills_tools(mcp_server, db):
    approve_leather_po()
    async with Client(mcp_server) as c:
        r = (await c.call_tool("sync_tally", {"file_paths": [db["todays_daybook"]]})).structured_content
        assert "7 new voucher(s)" in r["headline"]
        b = (await c.call_tool("get_daily_brief", {})).structured_content
        assert "₹2.10L sold" in b["headline"]
        h = (await c.call_tool("what_happened", {"start": "2026-11-20", "end": "2026-11-23"})).structured_content
        assert h["voucher_count"] == 14
        bills = (await c.call_tool("check_purchase_bills", {})).structured_content
        assert bills["overbilled_total"] == pytest.approx(4000 + 2050)
        run = (await c.call_tool("run_automation", {"job": "chase"})).structured_content
        assert run["ok"]
