"""Brief, timeline, bill checking and sync tools: the 'what happened' side of Grout."""
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from automations import jobs
from control.audit import audited
from control.roles import ASK, ONBOARD, requires
from db.models import Brief
from db.session import business_id, session_scope
from engine.brief import BriefLine, Happened, happened
from engine.matching import MatchResult
from tools import data

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


class BriefAnswer(BaseModel):
    headline: str
    for_date: date
    lines: list[BriefLine]
    sources: list[str]


class SyncAnswer(BaseModel):
    headline: str
    files: list[dict]


class AutomationAnswer(BaseModel):
    headline: str
    job: str
    ok: bool


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def get_daily_brief(day: date | None = None) -> BriefAnswer:
        """The owner's brief for a day (default: today so far): sales, money in, goods received and
        supplier bills, bill problems, overdue money, cash risk, low stock, orders due and approvals
        waiting. Built from the synced books (Tally). Read each line out; don't add numbers. Read-only."""
        with session_scope() as s:
            b = jobs.build_brief(s, day)
        return BriefAnswer(headline=b.headline, for_date=b.for_date, lines=b.lines, sources=b.sources)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def what_happened(start: date | None = None, end: date | None = None) -> Happened:
        """Everything booked between two dates (default: today): sales invoices, money received,
        supplier bills, goods received, payments made, each with what it changed. E.g. 'what did we
        buy this week?' -> start = 7 days ago. Read-only."""
        t = data.today()
        start = start or t
        end = end or start
        if end < start:
            raise ToolError("end must be on or after start")
        with session_scope() as s:
            return happened(data.vouchers(s), start, end)

    @mcp.tool(annotations=READ_ONLY)
    @audited
    @requires(ASK)
    async def check_purchase_bills() -> MatchResult:
        """Three-way check of every purchase: ordered (PO) vs received (goods receipt) vs billed
        (supplier bill in Tally). Lists bills that charge a different rate or quantity, bills with no
        PO, and deliveries that are late. Read-only (the automation drafts supplier queries)."""
        with session_scope() as s:
            return jobs.match_result(s)

    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def sync_tally(file_paths: list[str] | None = None) -> SyncAnswer:
        """Pull in new Tally exports now (normally automatic every minute). Optionally pass paths of
        Tally XML files (e.g. a Day Book export) to copy into the inbox first. Applies sales, payments,
        goods received and bills, then re-checks purchase bills."""
        from connectors.tally_sync import inbox_dir, sync_inbox

        INBOX = inbox_dir()
        INBOX.mkdir(parents=True, exist_ok=True)
        for fp in file_paths or []:
            p = Path(fp).expanduser()
            if not p.exists():
                raise ToolError(f"File not found: {fp}")
            shutil.copy(p, INBOX / p.name)
        with session_scope() as s:
            res = sync_inbox(s)
        if any(r["vouchers"] for r in res):
            jobs.run("match", "after_sync")
        n = sum(r["vouchers"] for r in res)
        head = f"Synced {len(res)} file(s), {n} new voucher(s)." if res else "No new Tally files in the inbox."
        return SyncAnswer(headline=head, files=res)

    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def run_automation(job: Literal["sync", "match", "brief", "chase"]) -> AutomationAnswer:
        """Run one automation now instead of waiting for its schedule: sync (Tally), match (check
        supplier bills, draft queries), brief (write and send today's brief to the owner), chase
        (draft payment reminders for late customers). Drafts still need the owner's approval."""
        r = jobs.run(job, "manual")
        return AutomationAnswer(headline=r.summary, job=job, ok=r.ok)

    @mcp.resource("business://brief/latest", mime_type="text/plain")
    def latest_brief() -> str:
        """The most recent brief sent to the owner."""
        with session_scope() as s:
            b = s.query(Brief).filter_by(business_id=business_id()).order_by(Brief.id.desc()).first()
            if b is None:
                return "No brief sent yet."
            return b.headline + "\n" + "\n".join(f"- {l['text']}" for l in b.lines_json)
