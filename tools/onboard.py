"""Onboard tools: pick an industry pack, import the business's files, fill profile gaps."""
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, create_model

from control.audit import audited
from control.roles import ONBOARD, requires
from db.models import Business, BusinessProfile, Customer, Invoice, Material, Product, Supplier
from db.session import business_id, session_scope
from importers import excel, tally
from packs.schema import list_packs, load_pack
from tools.data import profile_rows, today

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class ProfileResult(BaseModel):
    headline: str
    industry: str | None
    written: dict[str, Any]
    kept: list[str] = Field(default_factory=list, description="Keys not overwritten because the business already set them")


class Question(BaseModel):
    key: str
    question: str
    type: str
    unit: str
    options: list[str]
    current_default: Any | None


class GapsResult(BaseModel):
    headline: str
    questions: list[Question]
    asked_via_form: bool
    saved: dict[str, Any] = Field(default_factory=dict)


class ImportSummary(BaseModel):
    headline: str
    files: list[str]
    created: dict[str, int]
    updated: dict[str, int]
    notes: list[str]


def set_profile(sess, key: str, value: Any, source: str, confidence: float) -> None:
    row = sess.query(BusinessProfile).filter_by(business_id=business_id(), key=key).one_or_none()
    if row is None:
        sess.add(BusinessProfile(business_id=business_id(), key=key, value_json=value, source=source, confidence=confidence))
    else:
        row.value_json, row.source, row.confidence = value, source, confidence


def apply_pack_defaults(sess, industry: str) -> ProfileResult:
    pack = load_pack(industry)
    biz = sess.get(Business, business_id())
    if biz is None:
        biz = Business(id=business_id(), name="My business", industry=industry)
        sess.add(biz)
    biz.industry = industry
    existing = profile_rows(sess)
    written, kept = {}, []
    for p in pack.parameters:
        row = existing.get(p.key)
        if row is not None and not row.source.startswith("pack_default:"):
            kept.append(p.key)
            continue
        set_profile(sess, p.key, p.value, f"pack_default:{pack.key}", 0.3)
        written[p.key] = p.value
    return ProfileResult(
        headline=f"Loaded the {pack.name} pack: {len(written)} default(s) written, {len(kept)} kept from your own data.",
        industry=pack.key, written=written, kept=kept,
    )


def gaps(sess) -> list[Question]:
    biz = sess.get(Business, business_id())
    if biz is None or not biz.industry:
        raise ToolError("Pick an industry first with select_industry.")
    pack = load_pack(biz.industry)
    rows = profile_rows(sess)
    out = []
    for q in pack.onboarding_questions:
        row = rows.get(q.key)
        if row is None or row.source.startswith("pack_default:"):
            out.append(Question(key=q.key, question=q.question, type=q.type, unit=q.unit, options=q.options,
                                current_default=row.value_json if row else None))
    return out


def save_answers(sess, answers: dict[str, Any]) -> dict[str, Any]:
    biz = sess.get(Business, business_id())
    valid = {q.key: q for q in load_pack(biz.industry).onboarding_questions} if biz and biz.industry else {}
    saved = {}
    for key, val in answers.items():
        if key not in valid:
            raise ToolError(f"'{key}' is not an onboarding question. Valid keys: {', '.join(valid)}")
        q = valid[key]
        if key == "weekly_off_day" and isinstance(val, str) and val.strip().lower() in DAYS:
            val = DAYS.index(val.strip().lower())
        elif q.type == "number":
            try:
                val = float(str(val).replace(",", "").replace("₹", "").strip())
            except ValueError:
                raise ToolError(f"Answer for {key} must be a number, got {val!r}")
        set_profile(sess, key, val, "owner_confirmed", 1.0)
        saved[key] = val
    return saved


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):  # includes pandas Timestamp
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _upsert(sess, entity: str, recs: list[dict], source: str, created: dict, updated: dict) -> None:
    bid = business_id()
    model = {"materials": Material, "customers": Customer, "suppliers": Supplier, "products": Product}.get(entity)
    if model is not None:
        existing = {r.name.lower(): r for r in sess.query(model).filter_by(business_id=bid)}
        cols = set(model.__table__.columns.keys())
        for rec in recs:
            name = str(rec.get("name", "")).strip()
            if not name:
                continue
            row = existing.get(name.lower())
            fields = {k: v for k, v in rec.items() if k in cols and k != "name"}
            for k in ("on_hand", "unit_cost", "reorder_level", "selling_price"):
                if k in fields:
                    fields[k] = float(fields[k])
            for k in ("payment_terms_days", "lead_time_days"):
                if k in fields:
                    fields[k] = int(float(str(fields[k]).split()[0]))
            if row is None:
                if model is Material:
                    fields.setdefault("unit", "unit")
                row = model(business_id=bid, name=name, source=source, **fields)
                sess.add(row)
                existing[name.lower()] = row
                created[entity] = created.get(entity, 0) + 1
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
                row.source = source
                updated[entity] = updated.get(entity, 0) + 1
        sess.flush()
        return
    if entity == "invoices":
        custs = {c.name.lower(): c for c in sess.query(Customer).filter_by(business_id=bid)}
        by_no = {i.number: i for i in sess.query(Invoice).filter_by(business_id=bid) if i.number}
        for rec in recs:
            cname = str(rec.get("customer", "")).strip()
            if not cname:
                continue
            cust = custs.get(cname.lower())
            if cust is None:
                cust = Customer(business_id=bid, name=cname, source=source)
                sess.add(cust)
                sess.flush()
                custs[cname.lower()] = cust
                created["customers"] = created.get("customers", 0) + 1
            issued = _to_date(rec.get("issued_on")) or today()
            due = _to_date(rec.get("due_on")) or issued + timedelta(days=cust.payment_terms_days or 30)
            no = str(rec["number"]) if rec.get("number") is not None else None
            fields = dict(customer_id=cust.id, amount=float(rec["amount"]), gst_amount=float(rec.get("gst_amount") or 0),
                          issued_on=issued, due_on=due, paid_amount=float(rec.get("paid_amount") or 0), source=source)
            if no and no in by_no:
                for k, v in fields.items():
                    setattr(by_no[no], k, v)
                updated["invoices"] = updated.get("invoices", 0) + 1
            else:
                inv = Invoice(business_id=bid, number=no, status="open", **fields)
                sess.add(inv)
                if no:
                    by_no[no] = inv
                created["invoices"] = created.get("invoices", 0) + 1
        sess.flush()


def import_files(sess, file_paths: list[str]) -> ImportSummary:
    created: dict[str, int] = {}
    updated: dict[str, int] = {}
    notes: list[str] = []
    names = []
    order = ["suppliers", "customers", "products", "materials", "invoices"]
    for fp in file_paths:
        p = Path(fp).expanduser()
        if not p.exists():
            raise ToolError(f"File not found: {fp}")
        names.append(p.name)
        if tally.is_tally_xml(p):
            recs, n = tally.read_records(p)
        elif p.suffix.lower() in (".xlsx", ".xls", ".csv"):
            recs, n = excel.read_records(p)
        else:
            raise ToolError(f"Unsupported file type: {p.name} (use Tally XML, .xlsx or .csv)")
        notes += n
        source = f"import {p.name} {today().isoformat()}"
        for entity in order:
            if entity in recs:
                _upsert(sess, entity, recs[entity], source, created, updated)
    found = ", ".join(f"{v} {k}" for k, v in created.items()) or "nothing new"
    upd = ", ".join(f"{v} {k}" for k, v in updated.items()) or "nothing"
    return ImportSummary(headline=f"Imported {len(names)} file(s). New: {found}. Updated: {upd}.",
                         files=names, created=created, updated=updated, notes=notes)


def register(mcp: FastMCP) -> None:
    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def select_industry(industry: str) -> ProfileResult:
        """Choose the industry pack for this business (e.g. 'leather_footwear', 'laminate').
        Writes the pack's default parameters into the business profile without overwriting
        anything the business has already imported or confirmed."""
        if industry not in list_packs():
            raise ToolError(f"Unknown industry '{industry}'. Available packs: {', '.join(list_packs())}")
        with session_scope() as s:
            return apply_pack_defaults(s, industry)

    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def import_business_data(file_paths: list[str]) -> ImportSummary:
        """Import the business's own files. Auto-detects Tally XML exports vs Excel/CSV sheets,
        maps columns by header names and synonyms, and upserts customers, suppliers, products,
        materials and invoices. Returns what was found and how each column was mapped."""
        with session_scope() as s:
            return import_files(s, file_paths)

    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def get_profile_gaps(ctx: Context) -> GapsResult:
        """List the onboarding questions the business hasn't answered yet (values still on
        generic pack defaults). If the client supports forms (MCP elicitation) the questions are
        asked right away as a form and the answers saved; otherwise they are returned as JSON so
        you can ask the owner and then call confirm_profile."""
        with session_scope() as s:
            qs = gaps(s)
        if not qs:
            return GapsResult(headline="No gaps: every onboarding question is answered.", questions=[], asked_via_form=False)
        from control.approvals import client_supports_elicitation

        if client_supports_elicitation(ctx):
            fields = {
                q.key: ((float if q.type == "number" else str), Field(description=f"{q.question} ({q.unit})".strip(" ()")))
                for q in qs
            }
            Form = create_model("ProfileGaps", **fields)
            try:
                res = await ctx.elicit("A few facts about your factory so the numbers are yours, not industry averages:", Form)
            except Exception:
                res = None
            if res is not None and getattr(res, "action", None) == "accept":
                with session_scope() as s:
                    saved = save_answers(s, res.data.model_dump())
                return GapsResult(headline=f"Saved {len(saved)} answer(s) from the form.", questions=[], asked_via_form=True, saved=saved)
        return GapsResult(
            headline=f"{len(qs)} question(s) still open. Ask the owner, then call confirm_profile with the answers.",
            questions=qs, asked_via_form=False,
        )

    @mcp.tool
    @audited
    @requires(ONBOARD)
    async def confirm_profile(answers: dict[str, Any]) -> ProfileResult:
        """Save the owner's answers to onboarding questions, e.g.
        {"monthly_output_units": 8000, "opening_cash_balance": 650000, "weekly_off_day": "Sunday"}.
        Saved values are marked owner_confirmed and always beat pack defaults."""
        with session_scope() as s:
            saved = save_answers(s, answers)
            biz = s.get(Business, business_id())
        return ProfileResult(headline=f"Saved {len(saved)} owner-confirmed value(s).", industry=biz.industry if biz else None, written=saved)
