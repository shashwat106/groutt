"""The hero demo: Sharma Footwear gets an order for 2,000 Oxford Classics by the 10th of next month."""
import pytest
from fastmcp import Client

from db.models import Action, AuditLog
from db.session import session_scope


async def test_demo_scenario(mcp_server, db):
    due = db["demo_due_date"]
    async with Client(mcp_server) as c:
        r = (await c.call_tool("check_order_feasibility", {"product": "Oxford Classic", "qty": 2000, "due_date": due})).structured_content
        assert r["verdict"] == "feasible_with_extra_shift_and_purchase"
        assert r["extra_shifts_needed"] == 1
        assert r["bottleneck_stage"] == "stitching"
        leather = next(l for l in r["shortfalls"] if l["material"] == "Leather crust - tan")
        assert leather["short_qty"] == pytest.approx(400, rel=0.05)
        assert r["estimated_profit"] == pytest.approx(180000, rel=0.05)
        assert leather["supplier"] == "Agra Leather Traders" and leather["in_time"]
        assert "1 extra shift" in r["headline"]
        assert r["full"] is None and r["sources_total"] > 30

        po = (await c.call_tool("draft_purchase_order", {"items": [{"material": "Leather crust - tan", "qty": leather["short_qty"]}]})).structured_content
        assert po["status"] == "pending" and po["details"]["supplier"] == "Agra Leather Traders"
        with session_scope() as s:
            code = s.get(Action, po["action_id"]).approval_code  # what the owner reads off their screen
        ok = (await c.call_tool("approve_action", {"action_id": po["action_id"], "approval_code": code})).structured_content
        assert ok["status"] == "executed"

        # Re-check: the leather is now on order, so the shortfall is gone.
        r2 = (await c.call_tool("check_order_feasibility", {"product": "Oxford Classic", "qty": 2000, "due_date": due,
                                                            "detail": True})).structured_content
        assert r2["verdict"] == "feasible_with_extra_shift"
        assert r2["shortfalls"] == [] and r2["full"]["stock"]["short"] == []

    with session_scope() as s:
        tools = [a.tool for a in s.query(AuditLog).order_by(AuditLog.id)]
    assert tools == ["check_order_feasibility", "draft_purchase_order", "approve_action", "check_order_feasibility"]
