"""Grout Console (web app) tests, on a throwaway seeded database."""
import pytest
from fastapi.testclient import TestClient

from db.models import Action, AuditLog, PurchaseOrder
from db.session import session_scope


@pytest.fixture
def web(db):
    from console.app import app

    return TestClient(app)


@pytest.mark.parametrize("path", ["/", "/orders", "/check", "/approvals", "/production", "/stock", "/money", "/activity", "/api/pulse",
                                  "/brief", "/brief?day=2026-11-22", "/purchases", "/automations", "/connections"])
def test_pages_render(web, path):
    r = web.get(path)
    assert r.status_code == 200, r.text[:500]


def test_today_shows_attention_items(web):
    html = web.get("/").text
    assert "Bank balance goes below zero" in html
    assert "Raj Shoe Mart owes" in html
    assert "Rubber sole - Derby" in html


def test_check_order_then_draft_approve_undo(web, db):
    r = web.post("/check", data={"product": "Oxford Classic", "qty": "2000", "due_date": db["demo_due_date"], "price": "", "customer": ""})
    assert r.status_code == 200
    assert "Yes, with an extra shift and a material purchase" in r.text
    assert "bottleneck" in r.text
    with session_scope() as s:
        from db.models import Material
        leather = s.query(Material).filter_by(name="Leather crust - tan").one()
    r = web.post("/check/draft-po", data={"material_id": leather.id, "qty": "396.7"}, follow_redirects=False)
    assert r.status_code == 303
    aid = int(r.headers["location"].split("new=")[1])
    assert "Approve &amp; send" in web.get("/approvals").text
    with session_scope() as s:
        assert s.get(Action, aid).status == "pending"

    web.post(f"/approvals/{aid}/approve")
    with session_scope() as s:
        a = s.get(Action, aid)
        assert a.status == "executed" and a.approved_by_role == "owner (console)"
        assert s.get(PurchaseOrder, a.payload_json["po_id"]).status == "sent"
    web.post(f"/approvals/{aid}/undo")
    with session_scope() as s:
        assert s.get(Action, aid).status == "undone"
        tools = [x.tool for x in s.query(AuditLog).order_by(AuditLog.id)]
    assert tools == ["console:check_order", "console:draft_purchase_order", "console:approve_action", "console:undo_action"]


def test_demo_drop_syncs_and_updates_pages(web):
    r = web.post("/connections/demo-drop", follow_redirects=False)
    from urllib.parse import unquote
    assert r.status_code == 303 and "7 new voucher" in unquote(r.headers["location"])
    assert "Lucknow Footwear Bazaar" in web.get("/brief").text
    assert "Purchases &amp; bills" in web.get("/purchases").text


def test_reminder_from_money_page(web):
    from db.models import Customer
    with session_scope() as s:
        raj = s.query(Customer).filter_by(name="Raj Shoe Mart").one()
    r = web.post(f"/money/remind/{raj.id}", follow_redirects=False)
    assert r.status_code == 303
    assert "Namaste Raj Shoe Mart ji" in web.get("/approvals").text
