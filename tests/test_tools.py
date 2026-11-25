"""Tool tests through FastMCP's in-memory client."""
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from db.models import Action, AuditLog, Outbox, PurchaseOrder
from db.session import session_scope

FILES = Path(__file__).resolve().parent.parent / "seed" / "files"


def sc(result):
    return result.structured_content


async def test_onboarding_flow_end_to_end(mcp_server):
    async with Client(mcp_server) as c:
        r = sc(await c.call_tool("select_industry", {"industry": "leather_footwear"}))
        # seed already confirmed monthly output, cash and weekly off: those are kept
        assert "monthly_output_units" in r["kept"]
        imp = sc(await c.call_tool("import_business_data", {"file_paths": [str(FILES / "tally_export.xml"), str(FILES / "stock_and_parties.xlsx")]}))
        assert imp["created"]["customers"] >= 3  # Lucknow, Agra Shoe Emporium, Bhopal
        assert imp["created"]["suppliers"] == 1
        assert imp["created"]["invoices"] == 2
        assert imp["created"]["materials"] >= 4  # black leather, eyelets, toe puff, heel counter
        assert imp["updated"]["materials"] >= 2  # laces, insole board
        assert any("Stock Register: materials" in n for n in imp["notes"])
        gaps = sc(await c.call_tool("get_profile_gaps", {}))
        keys = {q["key"] for q in gaps["questions"]}
        assert keys == {"rejection_rate_pct", "cutting_wastage_pct", "extra_shift_cost_per_day"}
        assert gaps["asked_via_form"] is False
        conf = sc(await c.call_tool("confirm_profile", {"answers": {"rejection_rate_pct": 3, "cutting_wastage_pct": "12",
                                                                     "extra_shift_cost_per_day": "5,500"}}))
        assert conf["written"]["extra_shift_cost_per_day"] == 5500
        gaps2 = sc(await c.call_tool("get_profile_gaps", {}))
        assert gaps2["questions"] == []


async def test_profile_gaps_via_elicitation_form(mcp_server):
    async def handler(message, response_type, params, context):
        return {"rejection_rate_pct": 2.5, "cutting_wastage_pct": 9, "extra_shift_cost_per_day": 7000}

    async with Client(mcp_server, elicitation_handler=handler) as c:
        r = sc(await c.call_tool("get_profile_gaps", {}))
        assert r["asked_via_form"] is True
        assert r["saved"]["extra_shift_cost_per_day"] == 7000


async def test_viewer_is_denied_act_and_simulate(mcp_server, monkeypatch):
    monkeypatch.setenv("INDUSTRYOS_ROLE", "viewer")
    async with Client(mcp_server) as c:
        ok = sc(await c.call_tool("receivables", {}))  # Ask is allowed
        assert ok["receivables"]["total_outstanding"] > 0
        with pytest.raises(ToolError, match="Permission denied"):
            await c.call_tool("check_order_feasibility", {"product": "Oxford Classic", "qty": 10, "due_date": "2026-12-10"})
        with pytest.raises(ToolError, match="Permission denied"):
            await c.call_tool("draft_purchase_order", {"items": [{"material": "Laces", "qty": 10}]})
    with session_scope() as s:
        denied = s.query(AuditLog).filter_by(ok=False, role="viewer").count()
    assert denied == 2


async def test_manager_cannot_draft_po_accountant_cannot_approve_po(mcp_server, monkeypatch):
    monkeypatch.setenv("INDUSTRYOS_ROLE", "manager")
    async with Client(mcp_server) as c:
        with pytest.raises(ToolError, match="Permission denied"):
            await c.call_tool("draft_purchase_order", {"items": [{"material": "Laces", "qty": 10}]})
    monkeypatch.setenv("INDUSTRYOS_ROLE", "owner")
    async with Client(mcp_server) as c:
        aid = sc(await c.call_tool("draft_purchase_order", {"items": [{"material": "Laces", "qty": 10}]}))["action_id"]
    monkeypatch.setenv("INDUSTRYOS_ROLE", "accountant")
    async with Client(mcp_server) as c:
        with pytest.raises(ToolError, match="cannot approve"):
            await c.call_tool("approve_action", {"action_id": aid, "approval_code": "000000"})


async def test_act_stays_pending_until_approved_and_code_never_leaks(mcp_server):
    async with Client(mcp_server) as c:
        r = sc(await c.call_tool("draft_purchase_order", {"items": [{"material": "Leather crust - tan", "qty": 397}]}))
        assert r["status"] == "pending"
        aid = r["action_id"]
        with session_scope() as s:
            a = s.get(Action, aid)
            code = a.approval_code
            assert a.status == "pending"
            assert s.get(PurchaseOrder, r["details"]["po_id"]).status == "draft"
            assert s.query(Outbox).count() == 0
        assert code not in str(r)
        pend = await c.call_tool("list_pending_actions", {})
        assert code not in str(pend.structured_content)
        with pytest.raises(ToolError, match="Wrong approval code"):
            await c.call_tool("approve_action", {"action_id": aid, "approval_code": "999999" if code != "999999" else "111111"})
        with session_scope() as s:
            assert s.get(Action, aid).status == "pending"
        ok = sc(await c.call_tool("approve_action", {"action_id": aid, "approval_code": code}))
        assert ok["status"] == "executed"
        with session_scope() as s:
            assert s.get(PurchaseOrder, r["details"]["po_id"]).status == "sent"
            assert s.query(Outbox).filter_by(action_id=aid).count() == 1


async def test_in_chat_approval_via_elicitation(mcp_server):
    async def approve(message, response_type, params, context):
        return {"value": "Approve"}

    async def reject(message, response_type, params, context):
        return {"value": "Reject"}

    async with Client(mcp_server, elicitation_handler=approve) as c:
        r = sc(await c.call_tool("send_payment_reminder", {"customer": "Raj Shoe Mart"}))
        assert r["status"] == "executed"
        assert "Namaste Raj Shoe Mart ji" in r["details"]["message"]
    async with Client(mcp_server, elicitation_handler=reject) as c:
        r = sc(await c.call_tool("draft_purchase_order", {"items": [{"material": "Laces", "qty": 100}]}))
        assert r["status"] == "rejected"
        with session_scope() as s:
            assert s.get(PurchaseOrder, r["details"]["po_id"]).status == "cancelled"


async def test_undo_reverses_executed_po(mcp_server):
    async with Client(mcp_server) as c:
        r = sc(await c.call_tool("draft_purchase_order", {"items": [{"material": "Laces", "qty": 100}]}))
        with session_scope() as s:
            code = s.get(Action, r["action_id"]).approval_code
        await c.call_tool("approve_action", {"action_id": r["action_id"], "approval_code": code})
        u = sc(await c.call_tool("undo_action", {"action_id": r["action_id"]}))
        assert u["status"] == "undone"
        with session_scope() as s:
            assert s.get(PurchaseOrder, r["details"]["po_id"]).status == "cancelled"
            assert s.get(Action, r["action_id"]).status == "undone"
            assert s.query(Outbox).filter_by(action_id=r["action_id"]).one().status == "recalled"
        with pytest.raises(ToolError, match="Only executed"):
            await c.call_tool("undo_action", {"action_id": r["action_id"]})


async def test_draft_invoice_with_gst(mcp_server):
    async with Client(mcp_server) as c:
        r = sc(await c.call_tool("draft_invoice", {"order_id": 1}))
        assert r["status"] == "pending"
        assert r["details"]["taxable"] == 380000  # 400 x 950
        assert r["details"]["gst"] == 19000  # 5% pack default
        assert "pack default" in r["details"]["gst_source"]


async def test_every_call_is_audited(mcp_server):
    calls = [("true_cost_per_unit", {"product": "Oxford"}), ("stock_status", {}), ("receivables", {"overdue_days": 30}),
             ("cash_forecast", {"days": 30}), ("create_dashboard", {"open_in_browser": False}),
             ("create_tracker", {"workflow": "approvals", "open_in_browser": False})]
    async with Client(mcp_server) as c:
        for name, args in calls:
            await c.call_tool(name, args)
        with pytest.raises(ToolError):
            await c.call_tool("true_cost_per_unit", {"product": "Nonexistent boot"})
    with session_scope() as s:
        tools = [a.tool for a in s.query(AuditLog).order_by(AuditLog.id)]
        failed = s.query(AuditLog).filter_by(ok=False).count()
    assert tools == [n for n, _ in calls] + ["true_cost_per_unit"]
    assert failed == 1


async def test_ask_tools_carry_sources(mcp_server):
    async with Client(mcp_server) as c:
        cost = sc(await c.call_tool("true_cost_per_unit", {"product": "Oxford Classic"}))
        b = cost["breakdown"]
        assert b["total"] == pytest.approx(b["material"] + b["wastage"] + b["labour"] + b["overhead"] + b["rejection_allowance"])
        assert any("pack default" in s for s in cost["sources"])
        assert any("materials.Leather crust - tan" in s for s in cost["sources"])
        cash = sc(await c.call_tool("cash_forecast", {"days": 30}))
        assert cash["first_shortfall_date"] is not None  # seed squeezes cash before overdue money comes in
        assert any("owner confirmed: opening_cash_balance" in s for s in cash["sources"])
        st = sc(await c.call_tool("stock_status", {}))
        assert "Rubber sole - Derby" in st["headline"]


async def test_resources_and_prompts(mcp_server):
    async with Client(mcp_server) as c:
        uris = {str(r.uri) for r in await c.list_resources()}
        assert {"business://profile", "business://summary", "ui://industryos/dashboard"} <= uris
        glossary = await c.read_resource("industry://leather_footwear/glossary")
        assert "farma" in glossary[0].text
        process = await c.read_resource("industry://laminate/process")
        assert "multi_daylight_press" in process[0].text
        prompts = {p.name for p in await c.list_prompts()}
        assert {"onboard", "quote_order", "month_end_close", "daily_brief"} <= prompts
        tools = {t.name: t for t in await c.list_tools()}
        assert tools["create_dashboard"].meta["ui"]["resourceUri"] == "ui://industryos/dashboard"


async def test_second_pack_needs_no_code(mcp_server):
    async with Client(mcp_server) as c:
        r = sc(await c.call_tool("select_industry", {"industry": "laminate"}))
        assert r["industry"] == "laminate"
        assert r["written"]["max_shifts_per_day"] == 3
        with pytest.raises(ToolError, match="Unknown industry"):
            await c.call_tool("select_industry", {"industry": "spaceships"})
