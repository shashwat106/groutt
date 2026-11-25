"""Build tools: render screens (dashboard, tracker) from the business's real data.

MCP Apps choice (checked against the installed SDK at build time): FastMCP 2.14 lets a tool carry
`meta` and lets us register `ui://` resources with any mime type, so we register
`ui://industryos/dashboard` and `ui://industryos/tracker` as `text/html;profile=mcp-app`
resources and link them from the tools via `_meta.ui.resourceUri`. Clients that support MCP
Apps can render the screen inside the chat. Every client still gets the rendered file path and
a JSON summary, and the page opens in the local browser, so the demo works everywhere.
"""
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from control.audit import audited
from control.roles import BUILD, requires
from db.models import Action, AuditLog, Customer, Order, Product, PurchaseOrder, Supplier
from db.session import ROOT, business_id, session_scope
from engine import cashflow, stock
from engine.fmt import inr
from tools import data
from tools.ask import cash_for

UI_DIR = ROOT / "ui"
OUT = ROOT / "out"
APP_MIME = "text/html;profile=mcp-app"
DASHBOARD_URI = "ui://industryos/dashboard"
TRACKER_URI = "ui://industryos/tracker"
ALL_METRICS = ["cash", "receivables", "stock", "orders", "approvals", "audit"]

env = Environment(loader=FileSystemLoader(UI_DIR), autoescape=select_autoescape(["html", "j2"]))
env.filters["inr"] = inr
env.filters["inr_full"] = lambda v: inr(v, short=False)


class BuildResult(BaseModel):
    headline: str
    path: str
    ui_resource: str
    summary: dict
    html: str | None = None


def _spark(values: list[float], w: int = 520, h: int = 90) -> tuple[str, float]:
    """SVG polyline points for the cash line and the y of the zero line (layout maths, in code)."""
    if not values:
        return "", h
    lo, hi = min(values + [0]), max(values + [0])
    span = (hi - lo) or 1
    step = w / max(1, len(values) - 1)
    pts = " ".join(f"{i * step:.1f},{5 + h - (v - lo) / span * h:.1f}" for i, v in enumerate(values))
    return pts, round(5 + h - (0 - lo) / span * h, 1)


def dashboard_context(metrics: list[str] | None = None) -> dict:
    metrics = metrics or ALL_METRICS
    with session_scope() as s:
        biz = data.business(s)
        t = data.today()
        rec = cashflow.receivables(data.invoices(s), t, 0)
        st = stock.status(data.materials(s), data.bom(s), data.open_orders(s), data.params(s))
        fc = cash_for(s, 30)
        prods = {p.id: p.name for p in s.query(Product).filter_by(business_id=business_id())}
        custs = {c.id: c.name for c in s.query(Customer).filter_by(business_id=business_id())}
        orders = [
            {"id": o.id, "customer": custs.get(o.customer_id), "product": prods.get(o.product_id), "qty": o.qty,
             "due": o.due_date, "status": o.status, "value": o.qty * o.price}
            for o in s.query(Order).filter(Order.business_id == business_id(), Order.status.in_(data.OPEN_ORDER_STATUSES)).order_by(Order.due_date)
        ]
        pending = [{"id": a.id, "type": a.type, "summary": a.summary}
                   for a in s.query(Action).filter_by(business_id=business_id(), status="pending")]
        audit = [{"at": a.created_at.strftime("%d %b %H:%M"), "tool": a.tool, "role": a.role, "ok": a.ok, "result": a.result_summary[:110]}
                 for a in s.query(AuditLog).filter_by(business_id=business_id()).order_by(AuditLog.id.desc()).limit(10)]
        ctx = {
            "biz": biz.name, "industry": biz.industry, "city": biz.city, "today": t,
            "generated": datetime.now().strftime("%d %b %Y %H:%M"),
            "metrics": metrics, "rec": rec, "stock": st, "fc": fc, "orders": orders, "pending": pending, "audit": audit,
            "spark": _spark([d.balance for d in fc.days])[0],
            "zero_y": _spark([d.balance for d in fc.days])[1],
            "open_pairs": sum(o["qty"] for o in orders),
        }
    return ctx


def tracker_context(workflow: str) -> dict:
    with session_scope() as s:
        biz = data.business(s)
        if workflow == "orders":
            prods = {p.id: p.name for p in s.query(Product).filter_by(business_id=business_id())}
            custs = {c.id: c.name for c in s.query(Customer).filter_by(business_id=business_id())}
            cols = ["open", "in_production", "dispatched", "invoiced"]
            cards = [{"col": o.status, "title": f"#{o.id} {custs.get(o.customer_id)}",
                      "body": f"{o.qty:g} x {prods.get(o.product_id)}", "meta": f"due {o.due_date:%d %b}"}
                     for o in s.query(Order).filter_by(business_id=business_id())]
        elif workflow == "purchase_orders":
            sups = {x.id: x.name for x in s.query(Supplier).filter_by(business_id=business_id())}
            cols = ["draft", "approved", "sent", "received", "cancelled"]
            cards = [{"col": p.status, "title": f"PO-{p.id} {sups.get(p.supplier_id)}",
                      "body": ", ".join(f"{i['qty']:g} {i['unit']} {i['material']}" for i in p.items_json),
                      "meta": inr(p.total)} for p in s.query(PurchaseOrder).filter_by(business_id=business_id())]
        else:
            cols = ["pending", "executed", "rejected", "undone"]
            cards = [{"col": a.status, "title": f"#{a.id} {a.type.replace('_', ' ')}", "body": a.summary,
                      "meta": a.approved_by_role or a.requested_by_role}
                     for a in s.query(Action).filter_by(business_id=business_id())]
        return {"biz": biz.name, "workflow": workflow.replace("_", " "), "cols": cols,
                "cards": cards, "generated": datetime.now().strftime("%d %b %Y %H:%M")}


def _write(name: str, html: str, open_in_browser: bool) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    path.write_text(html, encoding="utf-8")
    if open_in_browser and os.environ.get("INDUSTRYOS_OPEN_BROWSER", "1") == "1":
        try:
            webbrowser.open(path.as_uri())
        except Exception:
            pass
    return path


def register(mcp: FastMCP) -> None:
    @mcp.resource(DASHBOARD_URI, mime_type=APP_MIME, name="Grout dashboard")
    def dashboard_ui() -> str:
        """Live dashboard (MCP Apps UI resource)."""
        return env.get_template("dashboard.html.j2").render(**dashboard_context())

    @mcp.resource(TRACKER_URI, mime_type=APP_MIME, name="Grout tracker")
    def tracker_ui() -> str:
        """Approvals tracker (MCP Apps UI resource)."""
        return env.get_template("tracker.html.j2").render(**tracker_context("actions"))

    @mcp.tool(annotations={"readOnlyHint": True}, meta={"ui": {"resourceUri": DASHBOARD_URI}, "ui/resourceUri": DASHBOARD_URI})
    @audited
    @requires(BUILD)
    async def create_dashboard(metrics: list[str] | None = None, open_in_browser: bool = True, include_html: bool = False) -> BuildResult:
        """Build a live dashboard from real data: cash forecast, receivables, stock, open orders,
        approvals waiting, and the audit trail. `metrics` picks sections from
        cash, receivables, stock, orders, approvals, audit (default: all). Opens in the browser."""
        bad = [m for m in (metrics or []) if m not in ALL_METRICS]
        if bad:
            from fastmcp.exceptions import ToolError
            raise ToolError(f"Unknown metric(s) {bad}. Choose from {ALL_METRICS}")
        ctx = dashboard_context(metrics)
        html = env.get_template("dashboard.html.j2").render(**ctx)
        path = _write("dashboard.html", html, open_in_browser)
        summary = {
            "cash_today": ctx["fc"].opening_balance, "cash_lowest_30d": ctx["fc"].lowest_balance,
            "first_shortfall_date": ctx["fc"].first_shortfall_date.isoformat() if ctx["fc"].first_shortfall_date else None,
            "receivables": ctx["rec"].total_outstanding, "overdue": ctx["rec"].total_overdue,
            "stock_value": ctx["stock"].total_value, "items_low": ctx["stock"].low_count,
            "open_orders": len(ctx["orders"]), "open_pairs": ctx["open_pairs"], "pending_approvals": len(ctx["pending"]),
        }
        return BuildResult(headline=f"Dashboard for {ctx['biz']} built: {path.name}", path=str(path),
                           ui_resource=DASHBOARD_URI, summary=summary, html=html if include_html else None)

    @mcp.tool(annotations={"readOnlyHint": True}, meta={"ui": {"resourceUri": TRACKER_URI}, "ui/resourceUri": TRACKER_URI})
    @audited
    @requires(BUILD)
    async def create_tracker(workflow: Literal["orders", "purchase_orders", "approvals"] = "orders",
                             open_in_browser: bool = True, include_html: bool = False) -> BuildResult:
        """Build a kanban-style tracker for a workflow: orders, purchase_orders or approvals."""
        ctx = tracker_context("actions" if workflow == "approvals" else workflow)
        html = env.get_template("tracker.html.j2").render(**ctx)
        path = _write(f"tracker_{workflow}.html", html, open_in_browser)
        counts = {c: sum(1 for k in ctx["cards"] if k["col"] == c) for c in ctx["cols"]}
        return BuildResult(headline=f"{workflow.replace('_', ' ').title()} tracker built: {path.name}", path=str(path),
                           ui_resource=TRACKER_URI, summary=counts, html=html if include_html else None)
