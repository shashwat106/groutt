"""Run the Sharma Footwear demo through the in-memory MCP client and print each tool's output.

    uv run python demo.py

Uses its own database (data/demo_run.db) so it never disturbs the one Claude Desktop uses.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / 'data' / 'demo_run.db').as_posix()}"
os.environ.setdefault("INDUSTRYOS_OPEN_BROWSER", "0")
os.environ.setdefault("INDUSTRYOS_ROLE", "owner")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from fastmcp import Client  # noqa: E402

from seed.sharma_footwear import seed  # noqa: E402


def show(step: str, data: dict, *extra: str) -> None:
    print(f"\n=== {step} ===")
    print(data.get("headline") or data.get("summary"))
    for key in extra:
        val = data
        for part in key.split("."):
            val = val[part] if isinstance(val, dict) else val[int(part)]
        print(f"  {key}: {val}")


async def main() -> None:
    info = seed()
    import server
    from db.models import Action
    from db.session import session_scope

    due = info["demo_due_date"]
    print(f"Sharma Footwear, today {info['today']}. Order: 2,000 Oxford Classic by {due}.")
    async with Client(server.mcp) as c:
        async def call(name, args=None):
            return (await c.call_tool(name, args or {})).structured_content

        r = await call("check_order_feasibility", {"product": "Oxford Classic", "qty": 2000, "due_date": due})
        show("1. Can we take it?  check_order_feasibility", r, "verdict", "unit_cost", "estimated_profit")
        print("  next actions:")
        for a in r["next_actions"]:
            print(f"   - {a}")
        print(f"  still on industry defaults: {len(r['still_on_industry_defaults'])} input(s); sources cited: {r['sources_total']}")
        for s in r["key_sources"]:
            print(f"   * {s}")

        short = r["shortfalls"][0]
        po = await call("draft_purchase_order", {"items": [{"material": short["material"], "qty": short["short_qty"]}]})
        show("2. Order the leather.  draft_purchase_order", po, "status", "next_step")

        with session_scope() as s:
            code = s.get(Action, po["action_id"]).approval_code
        print(f"\n  [owner's approval screen shows code {code}; owner reads it to the assistant]")
        ok = await call("approve_action", {"action_id": po["action_id"], "approval_code": code})
        show("3. Owner approves.  approve_action", ok)

        r2 = await call("check_order_feasibility", {"product": "Oxford Classic", "qty": 2000, "due_date": due})
        show("4. Re-check.  check_order_feasibility", r2, "verdict")

        w = await call("what_if", {"product": "Oxford Classic", "qty": 2000, "due_date": due, "changes": {"price_pct": 3}})
        show("5. What if we quote 3% higher?  what_if", w)

        show("6. Cash.  cash_forecast", await call("cash_forecast", {"days": 30}))
        show("7. Who owes us?  receivables", await call("receivables", {"overdue_days": 1}))
        d = await call("create_dashboard", {"open_in_browser": False})
        show("8. Dashboard.  create_dashboard", d, "path")
        audit = await call("get_audit_log", {"limit": 10})
        print("\n=== 9. Audit trail ===")
        for row in reversed(audit["rows"]):
            print(f"  {row['at']}  {row['role']:<6} {row['tool']:<24} {row['result'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
