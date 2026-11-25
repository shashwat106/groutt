"""The boring jobs Grout does by itself.

  sync   every minute   pull new Tally exports from inbox/tally and apply them
  match  every 10 min   check supplier bills against PO + goods received; draft a query for each new problem
  brief  daily 09:00    write yesterday's brief, show it in the Console, send it to the owner (outbox)
  chase  daily 10:00    draft payment reminders for customers past the chase limit (owner approves)

Every run is logged (automation_runs + audit_log). Anything that goes OUT to a customer or supplier is
only drafted; the owner approves it. The brief goes only to the owner, so it is sent directly.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from control import approvals
from control.audit import write_audit
from db.models import Action, AutomationRun, Brief, Business, Customer, Order, Outbox, Product, Supplier
from db.session import business_id, session_scope
from engine import cashflow, stock
from engine.brief import DailyBrief, daily_brief, happened
from engine.fmt import inr
from engine.matching import MatchResult, three_way
from tools import data

log = logging.getLogger("industryos.automations")

JOBS = {
    "sync": {"label": "Sync from Tally", "every_seconds": int(os.environ.get("INDUSTRYOS_SYNC_SECONDS", "60")),
             "what": "Reads new Tally exports (Day Book) and updates sales, payments, stock and purchases."},
    "match": {"label": "Check supplier bills", "every_seconds": 600,
              "what": "Matches every bill against the purchase order and goods received; drafts a query when they differ."},
    "brief": {"label": "Morning brief", "daily_at": os.environ.get("INDUSTRYOS_BRIEF_TIME", "09:00"),
              "what": "Writes yesterday's brief and sends it to the owner's WhatsApp."},
    "chase": {"label": "Chase payments", "daily_at": os.environ.get("INDUSTRYOS_CHASE_TIME", "10:00"),
              "what": "Drafts polite reminders for customers who are late; you approve before anything is sent."},
}


# ---------------- computations shared with the Console and MCP tools ----------------

def match_result(s) -> MatchResult:
    lines, rec, bills = data.matching_inputs(s)
    return three_way(lines, rec, bills, data.today())


def build_brief(s, day: date | None = None, today_mode: bool | None = None) -> DailyBrief:
    t = data.today()
    day = day or t
    today_mode = (day == t) if today_mode is None else today_mode
    biz = s.get(Business, business_id())
    h = happened(data.vouchers(s), day, day)
    rec = cashflow.receivables(data.invoices(s), t, 1)
    tops = sorted(rec.by_customer.items(), key=lambda kv: -kv[1])
    top_overdue = [(c, a, max(l.days_overdue for l in rec.lines if l.customer == c)) for c, a in tops]
    from tools.ask import cash_for

    fc = cash_for(s, 30)
    st = stock.status(data.materials(s), data.bom(s), data.open_orders(s), data.params(s))
    low = [(i.material, i.free, i.unit) for i in st.items if i.status != "ok"]
    mr = match_result(s)
    problems = [f"{r.supplier}, {r.material}: {'; '.join(r.flags)}" + (f" ({inr(r.difference)} extra)" if r.difference > 0 else "")
                for r in mr.exceptions]
    pending = s.query(Action).filter_by(business_id=business_id(), status="pending").count()
    prods = {p.id: p.name for p in s.query(Product).filter_by(business_id=business_id())}
    custs = {c.id: c.name for c in s.query(Customer).filter_by(business_id=business_id())}
    due = [(f"{custs.get(o.customer_id)} ({prods.get(o.product_id)})", o.qty, o.due_date)
           for o in s.query(Order).filter(Order.business_id == business_id(), Order.status.in_(data.OPEN_ORDER_STATUSES),
                                          Order.due_date <= t + timedelta(days=14)).order_by(Order.due_date)]
    return daily_brief(day, h, rec.total_overdue, top_overdue, fc.first_shortfall_date, fc.lowest_balance, low, problems,
                       pending, due, biz.name if biz else "Your business", today_mode=today_mode)


# ---------------- jobs ----------------

def job_sync(s) -> str:
    from connectors.tally_sync import sync_inbox

    results = sync_inbox(s)
    if not results:
        return "No new Tally files."
    n = sum(r["vouchers"] for r in results)
    return f"{len(results)} file(s), {n} new voucher(s): " + " | ".join(c for r in results for c in r["changes"][:6])


def job_match(s) -> str:
    mr = match_result(s)
    drafted = 0
    sups = {x.name.lower(): x for x in s.query(Supplier).filter_by(business_id=business_id())}
    existing = {a.payload_json.get("exception_key") for a in s.query(Action).filter_by(business_id=business_id(), type="supplier_query")}
    for r in mr.exceptions:
        if r.status == "late" or r.key in existing:
            continue
        sup = sups.get(r.supplier.lower())
        if sup is None:
            continue
        po = f"our PO-{r.po_id}" if r.po_id else "any purchase order of ours"
        ask = (f"Please issue a credit note for {inr(r.difference, False)} or send a corrected bill." if r.difference > 0
               else "Please confirm or correct the bill.")
        msg = (f"Dear {sup.name},\nWe checked your bill {', '.join(r.bills)} for {r.material} against {po}:\n"
               + "\n".join(f"- {f}" for f in r.flags) + f"\n{ask}\nRegards,\n{s.get(Business, business_id()).name}")
        summary = f"Bill query to {sup.name}: {r.material}, {'; '.join(r.flags)}" + (f" ({inr(r.difference)} extra)" if r.difference > 0 else "")
        approvals.create_action(s, "supplier_query", summary,
                                {"supplier_id": sup.id, "message": msg, "exception_key": r.key,
                                 "subject": f"Query on bill {', '.join(r.bills)}"}, "automation")
        drafted += 1
    return mr.headline + (f" Drafted {drafted} supplier quer{'y' if drafted == 1 else 'ies'} for approval." if drafted else "")


def job_brief(s, day: date | None = None, trigger: str = "schedule") -> str:
    t = data.today()
    day = day or (t - timedelta(days=1) if trigger == "schedule" else t)
    b = build_brief(s, day)
    biz = s.get(Business, business_id())
    to = (biz.owner_phone if biz else None) or "owner"
    s.add(Brief(business_id=business_id(), for_date=day, headline=b.headline, lines_json=[l.model_dump() for l in b.lines],
                trigger=trigger, delivered_to=to))
    s.add(Outbox(business_id=business_id(), channel="whatsapp", recipient=to, subject=f"Daily brief {day:%d %b}",
                 body=b.whatsapp_text))
    return f"Brief for {day:%d %b} sent to owner ({to}): {b.headline}"


def job_chase(s) -> str:
    t = data.today()
    prm = data.params(s)
    limit = int(float(prm["payment_chase_after_days"].value)) if "payment_chase_after_days" in prm else 30
    rec = cashflow.receivables(data.invoices(s), t, limit)
    recent = t - timedelta(days=7)
    custs = {c.name: c for c in s.query(Customer).filter_by(business_id=business_id())}
    blocked = set()
    for a in s.query(Action).filter_by(business_id=business_id(), type="payment_reminder"):
        if a.status == "pending" or (a.status == "executed" and a.created_at.date() >= recent):
            blocked.add(a.payload_json.get("customer_id"))
    from tools.act import create_reminder_draft

    drafted = []
    for name in rec.by_customer:
        c = custs.get(name)
        if c is None or c.id in blocked:
            continue
        create_reminder_draft(s, c, "automation")
        drafted.append(name)
    if not drafted:
        return f"No new reminders needed (limit {limit} days late)."
    return f"Drafted {len(drafted)} reminder(s) for approval: {', '.join(drafted)} (more than {limit} days late)."


RUNNERS = {"sync": job_sync, "match": job_match, "brief": job_brief, "chase": job_chase}


def run(job: str, trigger: str = "schedule") -> AutomationRun:
    if job not in RUNNERS:
        raise ValueError(f"Unknown job '{job}'. Jobs: {', '.join(RUNNERS)}")
    started = datetime.now(timezone.utc)
    ok, summary = True, ""
    try:
        with session_scope() as s:
            summary = RUNNERS[job](s, trigger=trigger) if job == "brief" else RUNNERS[job](s)
    except Exception as e:  # never let one job kill the scheduler
        ok, summary = False, f"failed: {e}"
        log.exception("job %s failed", job)
    with session_scope() as s:
        r = AutomationRun(business_id=business_id(), job=job, trigger=trigger, ok=ok, summary=summary[:2000],
                          started_at=started, finished_at=datetime.now(timezone.utc))
        s.add(r)
    write_audit(f"automation:{job}", {"trigger": trigger}, "automation", summary[:500], ok=ok)
    return r


def last_run(s, job: str) -> AutomationRun | None:
    return s.query(AutomationRun).filter_by(business_id=business_id(), job=job).order_by(AutomationRun.id.desc()).first()


def next_due(job: str, last: AutomationRun | None, now: datetime) -> datetime:
    spec = JOBS[job]
    if "every_seconds" in spec:
        if last is None:
            return now
        lt = last.started_at if last.started_at.tzinfo else last.started_at.replace(tzinfo=timezone.utc)
        return lt.astimezone() + timedelta(seconds=spec["every_seconds"])
    hh, mm = (int(x) for x in spec["daily_at"].split(":"))
    today_slot = datetime.combine(now.date(), time(hh, mm)).astimezone()
    ran_today = False
    if last is not None:
        lt = last.started_at if last.started_at.tzinfo else last.started_at.replace(tzinfo=timezone.utc)
        ran_today = lt.astimezone().date() == now.date() and last.trigger == "schedule"
    if ran_today or now < today_slot:
        return today_slot if not ran_today else today_slot + timedelta(days=1)
    return now


def run_due(now: datetime | None = None) -> list[str]:
    now = (now or datetime.now()).astimezone()
    ran = []
    for job in JOBS:
        with session_scope() as s:
            due = next_due(job, last_run(s, job), now)
        if due <= now:
            run(job, "schedule")
            ran.append(job)
    return ran
