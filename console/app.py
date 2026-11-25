"""Grout Console: the owner's web app. Same database and engine as the MCP server,
so the app and the AI can never disagree on a number.

Run:  uv run uvicorn console.app:app --port 8100
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from sqlalchemy import func  # noqa: E402

from control import approvals  # noqa: E402
from control.audit import write_audit  # noqa: E402
from db.models import Action, AuditLog, Customer, Material, Order, Product, PurchaseOrder, Supplier  # noqa: E402
from db.session import business_id, init_db, session_scope  # noqa: E402
from engine import capacity, cashflow, stock  # noqa: E402
from engine.fmt import inr, qty  # noqa: E402
from engine.whatif import evaluate_order  # noqa: E402
from tools import data  # noqa: E402
from tools.act import create_po_draft, create_reminder_draft  # noqa: E402
from tools.ask import cash_for  # noqa: E402

init_db()


@asynccontextmanager
async def lifespan(_app):
    """Runs the automations in the background while the Console is open (INDUSTRYOS_SCHEDULER=0 to disable)."""
    task = None
    if os.environ.get("INDUSTRYOS_SCHEDULER", "1") == "1":
        from automations.jobs import run_due

        async def loop():
            while True:
                try:
                    await asyncio.to_thread(run_due)
                except Exception:
                    logging.getLogger("industryos.console").exception("scheduler tick failed")
                await asyncio.sleep(15)

        task = asyncio.create_task(loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Grout Console", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["inr"] = inr
templates.env.filters["inr_full"] = lambda v: inr(v, short=False)
templates.env.filters["qty"] = qty
templates.env.filters["num"] = lambda v: f"{v:,.0f}"
templates.env.filters["unit"] = lambda u: (u or "").replace("_", " ")
templates.env.filters["d"] = lambda d: d.strftime("%d %b") if d else "—"
def _local(d):
    """DB timestamps are UTC (SQLite drops the zone); show them in local time."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone()
    return d


templates.env.filters["dt"] = lambda d: _local(d).strftime("%d %b, %H:%M") if d else "—"

ROLE = "owner"  # the person using the Console; real login comes later
STAGE_LABEL = {"clicking_press": "Cutting", "skiving_machine": "Skiving", "stitching_machine": "Stitching",
               "lasting_machine": "Lasting", "sole_press": "Soling", "finishing_station": "Finishing",
               "packing_table": "Packing"}


def nav_counts(s) -> dict:
    return {"pending": s.query(Action).filter_by(business_id=business_id(), status="pending").count()}


def render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    with session_scope() as s:
        biz = data.business(s)
        counts = nav_counts(s)
    return templates.TemplateResponse(request, name, {"active": active, "biz": biz, "counts": counts,
                                                      "today": data.today(), "stage_label": STAGE_LABEL, **ctx})


def bar(need: float, avail: float) -> dict:
    """Width/colour for a load bar (layout only)."""
    pct = need / avail * 100 if avail else 0
    return {"pct": round(pct), "w": min(100, round(pct)), "tone": "bad" if pct > 100 else ("warn" if pct > 85 else "ok")}


def floor_load(s, days: int = 14) -> capacity.LoadResult:
    t = data.today()
    prm = data.params(s)
    window_end = t + timedelta(days=days)
    orders = [o for o in data.open_orders(s) if o.due_date <= window_end]
    return capacity.load(orders, data.routing(s), data.machines(s), data.calendar(prm, t, window_end), prm)


# ---------------- Today ----------------

@app.get("/", response_class=HTMLResponse)
def today_page(request: Request):
    with session_scope() as s:
        t = data.today()
        fc = cash_for(s, 30)
        rec = cashflow.receivables(data.invoices(s), t, 1)
        st = stock.status(data.materials(s), data.bom(s), data.open_orders(s), data.params(s))
        load = floor_load(s)
        pending = s.query(Action).filter_by(business_id=business_id(), status="pending").order_by(Action.id.desc()).all()
        custs = {c.name: c.id for c in s.query(Customer).filter_by(business_id=business_id())}
        prods = {p.id: p.name for p in s.query(Product).filter_by(business_id=business_id())}
        cnames = {c.id: c.name for c in s.query(Customer).filter_by(business_id=business_id())}
        orders = s.query(Order).filter(Order.business_id == business_id(), Order.status.in_(data.OPEN_ORDER_STATUSES)).order_by(Order.due_date).all()

        attention = []
        for a in pending:
            attention.append({"tone": "action", "tag": "Approve", "title": a.summary,
                              "sub": f"Drafted by {'Grout automation' if a.requested_by_role == 'automation' else a.requested_by_role} · {_local(a.created_at):%d %b, %H:%M}", "href": "/approvals",
                              "cta": "Review"})
        if fc.first_shortfall_date:
            attention.append({"tone": "bad", "tag": "Cash", "title": f"Bank balance goes below zero on {fc.first_shortfall_date:%d %b}",
                              "sub": f"Lowest {inr(fc.lowest_balance)} on {fc.lowest_on:%d %b}. Overdue money would fix it.",
                              "href": "/money", "cta": "See cash"})
        for cust, amt in sorted(rec.by_customer.items(), key=lambda kv: -kv[1])[:2]:
            late = max(l.days_overdue for l in rec.lines if l.customer == cust)
            attention.append({"tone": "warn", "tag": "Collect", "title": f"{cust} owes {inr(amt)}",
                              "sub": f"Oldest invoice {late} days late", "href": "/money", "cta": "Remind",
                              "post": f"/money/remind/{custs.get(cust)}"})
        for i in st.items:
            if i.status != "ok":
                attention.append({"tone": "warn", "tag": "Stock", "title": f"{i.material}: only {qty(i.free, i.unit)} free",
                                  "sub": f"Below reorder level of {qty(i.reorder_level, i.unit)} after open orders",
                                  "href": "/stock", "cta": "View"})

        from automations.jobs import build_brief
        brief = build_brief(s, t)
        open_rows = [{"id": o.id, "customer": cnames.get(o.customer_id), "product": prods.get(o.product_id), "qty": o.qty,
                      "due": o.due_date, "days_left": (o.due_date - t).days, "status": o.status} for o in orders]
        peak = max(load.loads, key=lambda l: l.utilisation_pct) if load.loads else None
        kpis = {
            "cash_low": fc.lowest_balance, "cash_low_on": fc.lowest_on, "cash_today": fc.opening_balance,
            "overdue": rec.total_overdue, "overdue_n": len(rec.by_customer),
            "open_pairs": sum(o.qty for o in orders), "open_n": len(orders),
            "load_pct": peak.utilisation_pct if peak else 0, "load_stage": STAGE_LABEL.get(peak.machine_type, peak.machine_type) if peak else "—",
        }
    return render(request, "today.html", "today", attention=attention, kpis=kpis, open_rows=open_rows, brief=brief)


# ---------------- Orders ----------------

@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request):
    with session_scope() as s:
        t = data.today()
        prods = {p.id: p.name for p in s.query(Product).filter_by(business_id=business_id())}
        cnames = {c.id: c.name for c in s.query(Customer).filter_by(business_id=business_id())}
        rows = [{"id": o.id, "customer": cnames.get(o.customer_id), "product": prods.get(o.product_id), "qty": o.qty,
                 "due": o.due_date, "days_left": (o.due_date - t).days, "status": o.status, "value": o.qty * o.price}
                for o in s.query(Order).filter_by(business_id=business_id()).order_by(Order.due_date.desc())]
    stages = ["open", "in_production", "dispatched", "invoiced"]
    pipeline = [{"key": k, "n": sum(1 for r in rows if r["status"] == k), "pairs": sum(r["qty"] for r in rows if r["status"] == k)} for k in stages]
    return render(request, "orders.html", "orders", rows=rows, pipeline=pipeline)


def _check_form_defaults(s) -> dict:
    t = data.today()
    y, m = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
    return {"products": [p.name for p in s.query(Product).filter_by(business_id=business_id())],
            "customers": [c.name for c in s.query(Customer).filter_by(business_id=business_id())],
            "default_due": date(y, m, 10).isoformat()}


@app.get("/check", response_class=HTMLResponse)
def check_page(request: Request):
    with session_scope() as s:
        form = _check_form_defaults(s)
    return render(request, "check.html", "check", form=form, result=None, inp={})


@app.post("/check", response_class=HTMLResponse)
def check_run(request: Request, product: str = Form(...), qty_: float = Form(..., alias="qty"),
              due_date: str = Form(...), price: str = Form(""), customer: str = Form("")):
    inp = {"product": product, "qty": qty_, "due_date": due_date, "price": price, "customer": customer}
    with session_scope() as s:
        form = _check_form_defaults(s)
        p = data.find_one(s, Product, product, "product")
        ev = evaluate_order(data.scenario(s, p, qty_, date.fromisoformat(due_date), float(price) if price else None))
    write_audit("console:check_order", inp, ROLE, ev.headline)
    stages = [{"label": STAGE_LABEL.get(st.machine_type, st.stage), "need": st.hours_needed,
               "one": st.hours_per_shift_window, "bar": bar(st.hours_needed, st.hours_per_shift_window),
               "shifts": st.shifts_needed, "new": st.hours_new_order, "open": st.hours_open_orders,
               "is_bottleneck": st.stage == ev.capacity.bottleneck_stage} for st in ev.capacity.stages]
    return render(request, "check.html", "check", form=form, result=ev, stages=stages, inp=inp)


@app.post("/check/draft-po")
def check_draft_po(material_id: int = Form(...), qty_: float = Form(..., alias="qty")):
    with session_scope() as s:
        m = s.get(Material, material_id)
        d = create_po_draft(s, [(m, qty_, None)], None, ROLE)
    write_audit("console:draft_purchase_order", {"material": m.name, "qty": qty_}, ROLE, d["summary"])
    return RedirectResponse(f"/approvals?new={d['action_id']}", status_code=303)


# ---------------- Approvals ----------------

@app.get("/approvals", response_class=HTMLResponse)
def approvals_page(request: Request, new: int | None = None, done: str | None = None):
    with session_scope() as s:
        acts = s.query(Action).filter_by(business_id=business_id()).order_by(Action.id.desc()).all()
        pending = [a for a in acts if a.status == "pending"]
        history = [a for a in acts if a.status != "pending"][:25]
        details = {}
        for a in pending:
            if a.type == "purchase_order":
                po = s.get(PurchaseOrder, a.payload_json["po_id"])
                sup = s.get(Supplier, po.supplier_id)
                details[a.id] = {"kind": "po", "lines": po.items_json, "total": po.total, "supplier": sup.name,
                                 "expected": po.expected_on, "due": po.due_on}
            elif a.type in ("payment_reminder", "supplier_query"):
                details[a.id] = {"kind": "msg", "message": a.payload_json["message"]}
    return render(request, "approvals.html", "approvals", pending=pending, history=history, details=details, new=new, done=done)


def _decide(action_id: int, how: str) -> str:
    with session_scope() as s:
        a = s.get(Action, action_id)
        if a is None or a.business_id != business_id():
            raise HTTPException(404)
        try:
            if how == "approve":
                msg = approvals.execute(s, a, f"{ROLE} (console)")
            elif how == "reject":
                msg = approvals.reject(s, a, f"{ROLE} (console)")
            else:
                msg = approvals.undo(s, a)
        except ValueError as e:
            msg = str(e)
    write_audit(f"console:{how}_action", {"action_id": action_id}, ROLE, msg)
    return msg


@app.post("/approvals/{action_id}/{how}")
def approvals_decide(action_id: int, how: str):
    if how not in ("approve", "reject", "undo"):
        raise HTTPException(404)
    msg = _decide(action_id, how)
    return RedirectResponse(f"/approvals?done={msg}", status_code=303)


# ---------------- Production / Stock / Money / Activity ----------------

@app.get("/production", response_class=HTMLResponse)
def production_page(request: Request, days: int = 14):
    with session_scope() as s:
        load = floor_load(s, days)
    rows = [{"label": STAGE_LABEL.get(l.machine_type, l.machine_type), "l": l, "bar": bar(l.hours_needed, l.hours_available)}
            for l in load.loads]
    return render(request, "production.html", "production", rows=rows, load=load, days=days)


@app.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request):
    with session_scope() as s:
        st = stock.status(data.materials(s), data.bom(s), data.open_orders(s), data.params(s))
        sups = {x.id: x.name for x in s.query(Supplier).filter_by(business_id=business_id())}
        pos = [{"id": p.id, "supplier": sups.get(p.supplier_id), "lines": p.items_json, "total": p.total, "status": p.status,
                "expected": p.expected_on} for p in s.query(PurchaseOrder).filter_by(business_id=business_id()).order_by(PurchaseOrder.id.desc())]
    return render(request, "stock.html", "stock", st=st, pos=pos)


@app.get("/money", response_class=HTMLResponse)
def money_page(request: Request, done: str | None = None):
    with session_scope() as s:
        t = data.today()
        rec = cashflow.receivables(data.invoices(s), t, 0)
        fc = cash_for(s, 30)
        custs = {c.name: c.id for c in s.query(Customer).filter_by(business_id=business_id())}
    vals = [d.balance for d in fc.days]
    lo, hi = min(vals + [0]), max(vals + [0])
    span = (hi - lo) or 1
    w, h = 720, 160
    step = w / max(1, len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{10 + h - (v - lo) / span * h:.1f}" for i, v in enumerate(vals))
    zero_y = round(10 + h - (0 - lo) / span * h, 1)
    overdue_by_c = {}
    for l in rec.lines:
        if l.days_overdue > 0:
            overdue_by_c[l.customer] = overdue_by_c.get(l.customer, 0) + l.outstanding
    return render(request, "money.html", "money", rec=rec, fc=fc, pts=pts, zero_y=zero_y, custs=custs,
                  overdue_by_c=overdue_by_c, done=done, chart_w=w, chart_h=h + 20)


@app.post("/money/remind/{customer_id}")
def money_remind(customer_id: int):
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            raise HTTPException(404)
        try:
            d = create_reminder_draft(s, c, ROLE)
        except Exception as e:
            return RedirectResponse(f"/money?done={e}", status_code=303)
    write_audit("console:send_payment_reminder", {"customer": c.name}, ROLE, d["summary"])
    return RedirectResponse(f"/approvals?new={d['action_id']}", status_code=303)


@app.get("/activity", response_class=HTMLResponse)
def activity_page(request: Request):
    with session_scope() as s:
        rows = s.query(AuditLog).filter_by(business_id=business_id()).order_by(AuditLog.id.desc()).limit(100).all()
        rows = [{"at": r.created_at, "tool": r.tool, "role": r.role, "ok": r.ok, "result": r.result_summary,
                 "via": "Console" if r.tool.startswith("console:") else ("Automation" if r.tool.startswith("automation:") else "AI assistant")}
                for r in rows]
    return render(request, "activity.html", "activity", rows=rows)


# ---------------- Brief / Purchases / Automations / Connections ----------------

@app.get("/brief", response_class=HTMLResponse)
def brief_page(request: Request, day: str | None = None, done: str | None = None):
    from automations.jobs import build_brief
    from db.models import Brief

    d = date.fromisoformat(day) if day else data.today()
    with session_scope() as s:
        b = build_brief(s, d)
        from engine.brief import happened
        h = happened(data.vouchers(s), d, d)
        sent = s.query(Brief).filter_by(business_id=business_id()).order_by(Brief.id.desc()).limit(10).all()
        sent = [{"for_date": x.for_date, "headline": x.headline, "to": x.delivered_to, "at": x.created_at, "trigger": x.trigger} for x in sent]
    timeline = sorted(h.sales + h.money_in + h.goods_in + h.bills + h.money_out, key=lambda v: v.vtype)
    return render(request, "brief.html", "brief", b=b, h=h, timeline=timeline, day=d, sent=sent, done=done,
                  prev=(d - timedelta(days=1)).isoformat(), next=(d + timedelta(days=1)).isoformat() if d < data.today() else None)


@app.post("/brief/send")
def brief_send():
    from automations.jobs import run
    r = run("brief", "manual")
    return RedirectResponse(f"/brief?done={r.summary[:160]}", status_code=303)


@app.get("/purchases", response_class=HTMLResponse)
def purchases_page(request: Request):
    from automations.jobs import match_result
    with session_scope() as s:
        mr = match_result(s)
        queries = {a.payload_json.get("exception_key"): a.status
                   for a in s.query(Action).filter_by(business_id=business_id(), type="supplier_query")}
    order = {"exception": 0, "bill_without_po": 1, "late": 2, "awaiting_bill": 3, "awaiting_delivery": 4, "matched": 5}
    rows = sorted(mr.rows, key=lambda r: (order.get(r.status, 9), -(r.po_id or 0)))
    return render(request, "purchases.html", "purchases", mr=mr, rows=rows, queries=queries)


@app.get("/automations", response_class=HTMLResponse)
def automations_page(request: Request, done: str | None = None):
    from automations.jobs import JOBS, last_run, next_due
    from db.models import AutomationRun
    now = datetime.now().astimezone()
    with session_scope() as s:
        jobs_view = []
        for key, spec in JOBS.items():
            lr = last_run(s, key)
            jobs_view.append({"key": key, "spec": spec, "last": lr, "next": next_due(key, lr, now),
                              "schedule": f"every {spec['every_seconds'] // 60 or 1} min" if "every_seconds" in spec else f"daily at {spec['daily_at']}"})
        runs = s.query(AutomationRun).filter_by(business_id=business_id()).order_by(AutomationRun.id.desc()).limit(30).all()
        drafted = s.query(Action).filter(Action.business_id == business_id(), Action.requested_by_role == "automation").count()
    return render(request, "automations.html", "automations", jobs=jobs_view, runs=runs, done=done, drafted=drafted,
                  scheduler_on=os.environ.get("INDUSTRYOS_SCHEDULER", "1") == "1")


@app.post("/automations/{job}/run")
def automations_run(job: str):
    from automations.jobs import JOBS, run
    if job not in JOBS:
        raise HTTPException(404)
    r = run(job, "manual")
    return RedirectResponse(f"/automations?done={JOBS[job]['label']}: {r.summary[:200]}", status_code=303)


@app.get("/connections", response_class=HTMLResponse)
def connections_page(request: Request, done: str | None = None):
    from connectors.tally_sync import inbox_dir
    from db.models import SyncFile, Voucher
    with session_scope() as s:
        files = s.query(SyncFile).filter_by(business_id=business_id()).order_by(SyncFile.id.desc()).limit(20).all()
        vcount = s.query(Voucher).filter_by(business_id=business_id()).count()
        last_v = s.query(func.max(Voucher.created_at)).filter_by(business_id=business_id()).scalar()
        recent = s.query(Voucher).filter_by(business_id=business_id()).order_by(Voucher.id.desc()).limit(12).all()
    waiting = sorted(p.name for p in inbox_dir().glob("*.xml")) if inbox_dir().exists() else []
    demo_files = sorted((ROOT / "seed" / "files" / "generated").glob("*.xml"))
    return render(request, "connections.html", "connections", files=files, vcount=vcount, last_v=last_v, recent=recent,
                  inbox=str(inbox_dir()), waiting=waiting, done=done, demo_file=demo_files[-1].name if demo_files else None)


@app.post("/connections/upload")
async def connections_upload(file: UploadFile = File(...)):
    from automations.jobs import run
    from connectors.tally_sync import inbox_dir
    name = Path(file.filename or "upload.xml").name
    if not name.lower().endswith(".xml"):
        return RedirectResponse("/connections?done=Please upload a Tally XML export (.xml)", status_code=303)
    inbox_dir().mkdir(parents=True, exist_ok=True)
    (inbox_dir() / name).write_bytes(await file.read())
    r = run("sync", "manual")
    run("match", "after_sync")
    return RedirectResponse(f"/connections?done={r.summary[:220]}", status_code=303)


@app.post("/connections/demo-drop")
def connections_demo_drop():
    """Demo helper: simulate the accountant exporting today's Day Book from Tally into the inbox."""
    import shutil
    from automations.jobs import run
    from connectors.tally_sync import inbox_dir
    f = ROOT / "seed" / "files" / "generated" / f"tally_daybook_{data.today():%Y%m%d}.xml"
    if not f.exists():
        return RedirectResponse("/connections?done=No demo Day Book for today; run the seed first", status_code=303)
    inbox_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy(f, inbox_dir() / f.name)
    r = run("sync", "manual")
    run("match", "after_sync")
    return RedirectResponse(f"/connections?done={r.summary[:220]}", status_code=303)


@app.get("/api/pulse")
def pulse():
    """Lets open pages refresh themselves when the AI drafts or approves something."""
    with session_scope() as s:
        last = s.query(func.max(AuditLog.id)).filter_by(business_id=business_id()).scalar() or 0
        pending = s.query(Action).filter_by(business_id=business_id(), status="pending").count()
    return JSONResponse({"last_audit": last, "pending": pending})
