"""Grout MCP server.

Run:  uv run fastmcp run server.py                     (stdio, for Claude Desktop)
      uv run fastmcp run server.py --transport http --port 8000   (streamable HTTP)
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Windows pipes default to cp1252, which can't print the rupee sign in logs.
for _stream in (sys.stderr,):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastmcp import FastMCP  # noqa: E402

from db.session import business_id, init_db, session_scope  # noqa: E402
from packs.schema import load_pack, validate_all  # noqa: E402
from tools import act, ask, automate, build, onboard, simulate  # noqa: E402
from tools import data  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(name)s %(message)s")

INSTRUCTIONS = """Grout is the operating brain of a small manufacturing business.
Rules for you, the assistant:
- Never do arithmetic yourself. Every number must come from a tool result; quote it and its sources.
- Read the `headline` field out to the user; offer details (breakdown, sources) on request.
- Ask/Simulate/Build tools are read-only. Act tools only create drafts that a human approves.
- If an Act tool returns status 'pending', ask the owner for the 6-digit approval code shown on
  their approval screen. Never guess a code and never approve on the owner's behalf.
- Mention when an answer still relies on generic industry defaults (the `assumptions` field).
- For "what happened today / yesterday / this week" use get_daily_brief or what_happened; data comes
  from the business's books (Tally), synced automatically."""

mcp = FastMCP("Grout", instructions=INSTRUCTIONS)

PACKS = validate_all()  # fail fast if any pack YAML is invalid
init_db()

for module in (onboard, ask, simulate, act, build, automate):
    module.register(mcp)

# Token diet: tool replies are still structured JSON, but we don't publish a full reply schema for
# every tool. Those schemas were ~2/3 of the tool definitions an AI client loads into every chat.
for _tool in mcp._tool_manager._tools.values():
    _tool.output_schema = None


# ---------------- resources ----------------

@mcp.resource("business://profile", mime_type="application/json")
def business_profile() -> str:
    """The living business profile: each value with its source and confidence."""
    with session_scope() as s:
        rows = data.profile_rows(s)
        return json.dumps({k: {"value": r.value_json, "source": data.profile_source_label(r), "confidence": r.confidence}
                           for k, r in rows.items()}, indent=2, default=str)


@mcp.resource("business://summary", mime_type="application/json")
def business_summary() -> str:
    """Headline numbers for the business right now (cash, receivables, stock, orders, approvals)."""
    ctx = build.dashboard_context()
    return json.dumps({
        "business": ctx["biz"], "industry": ctx["industry"], "as_of": str(ctx["today"]),
        "cash_today": ctx["fc"].opening_balance, "first_cash_shortfall": str(ctx["fc"].first_shortfall_date),
        "receivables": ctx["rec"].total_outstanding, "overdue": ctx["rec"].total_overdue,
        "stock_value": ctx["stock"].total_value, "items_low": ctx["stock"].low_count,
        "open_orders": len(ctx["orders"]), "pending_approvals": len(ctx["pending"]),
    }, indent=2)


@mcp.resource("industry://{pack}/process", mime_type="application/json")
def industry_process(pack: str) -> str:
    """Production stages of an industry pack, in order, with typical machine and minutes per unit."""
    p = load_pack(pack)
    return json.dumps({"pack": p.name, "unit": p.unit_of_output,
                       "stages": [s.model_dump() for s in p.stages],
                       "standards": [s.model_dump() for s in p.standards]}, indent=2)


@mcp.resource("industry://{pack}/glossary", mime_type="application/json")
def industry_glossary(pack: str) -> str:
    """Trade terms for an industry pack, with local shop-floor words."""
    p = load_pack(pack)
    return json.dumps([g.model_dump() for g in p.glossary], indent=2, ensure_ascii=False)


# ---------------- prompts ----------------

@mcp.prompt(name="onboard")
def onboard_prompt() -> str:
    """Guided setup for a new business."""
    return ("Help me set up Grout for my factory. Steps: 1) ask which industry and call select_industry; "
            "2) ask me for my Tally XML / Excel files and call import_business_data, then summarise what was found; "
            "3) call get_profile_gaps and ask me the open questions, then confirm_profile; "
            "4) finish with create_dashboard.")


@mcp.prompt
def quote_order(customer: str, product: str, qty: int, due_date: str = "") -> str:
    """Decide whether to accept an order and what to quote."""
    return (f"{customer} wants {qty} pairs of {product}{' by ' + due_date if due_date else ''}. "
            "Call check_order_feasibility, read out the headline, explain the bottleneck and any shortfall, "
            "and propose next actions. If material is short, offer to draft_purchase_order from the preferred supplier. "
            "Use only numbers from tool results.")


@mcp.prompt
def month_end_close(month: str) -> str:
    """Month-end review: receivables, cash, stock and pending approvals."""
    return (f"Do a month-end close review for {month}. Call receivables(overdue_days=30), cash_forecast(days=30), "
            "stock_status(), list_pending_actions() and get_audit_log(). Summarise risks and suggest which payment "
            "reminders to draft. Do not draft anything without asking me.")


@mcp.prompt
def daily_brief() -> str:
    """Morning brief for the owner."""
    return ("Give me my brief: call get_daily_brief (today so far, or yesterday if I ask) and read the lines out "
            "simply. If there are bill problems or late payments, offer to show check_purchase_bills or the drafts "
            "waiting for approval. Use only numbers from the tools.")


if __name__ == "__main__":
    mcp.run(show_banner=False)  # no banner / update check on startup
