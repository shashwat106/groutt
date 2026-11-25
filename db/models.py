"""SQLAlchemy models. Same models run on SQLite (local/tests) and Postgres (Supabase).
One workspace per business: every business table has business_id."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(100))
    owner_phone: Mapped[str | None] = mapped_column(String(50))  # where the daily brief goes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BizMixin:
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id"), index=True)


class BusinessProfile(BizMixin, Base):
    """Living profile: pack defaults first, then overridden by imported/confirmed values."""

    __tablename__ = "business_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(100))
    value_json: Mapped[object] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(200))  # pack_default | owner_confirmed | import:<file>
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Product(BizMixin, Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20), default="pair")
    selling_price: Mapped[float] = mapped_column(Float, default=0)
    product_type: Mapped[str | None] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(200), default="manual")


class Material(BizMixin, Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    on_hand: Mapped[float] = mapped_column(Float, default=0)
    reorder_level: Mapped[float] = mapped_column(Float, default=0)
    unit_cost: Mapped[float] = mapped_column(Float, default=0)
    preferred_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    source: Mapped[str] = mapped_column(String(200), default="manual")


class BomItem(BizMixin, Base):
    __tablename__ = "bom_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    qty_per_unit: Mapped[float] = mapped_column(Float)
    wastage_pct: Mapped[float | None] = mapped_column(Float)


class RoutingStepRow(BizMixin, Base):
    __tablename__ = "routing_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(50))
    machine_type: Mapped[str] = mapped_column(String(50))
    minutes_per_unit: Mapped[float] = mapped_column(Float)


class Machine(BizMixin, Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_type: Mapped[str] = mapped_column(String(50))
    count: Mapped[int] = mapped_column(Integer)
    hours_per_shift: Mapped[float] = mapped_column(Float, default=8)
    shifts_per_day: Mapped[int] = mapped_column(Integer, default=1)
    efficiency_pct: Mapped[float] = mapped_column(Float, default=85)


class Labour(BizMixin, Base):
    __tablename__ = "labour"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(100))
    headcount: Mapped[int] = mapped_column(Integer)
    cost_per_hour: Mapped[float] = mapped_column(Float)


class Customer(BizMixin, Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(20))
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    source: Mapped[str] = mapped_column(String(200), default="manual")


class Supplier(BizMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(20))
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    source: Mapped[str] = mapped_column(String(200), default="manual")


class Order(BizMixin, Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[float] = mapped_column(Float)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | in_production | dispatched | invoiced
    price: Mapped[float] = mapped_column(Float)


class Invoice(BizMixin, Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str | None] = mapped_column(String(50))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    amount: Mapped[float] = mapped_column(Float)  # incl. GST
    gst_amount: Mapped[float] = mapped_column(Float, default=0)
    issued_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    paid_amount: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="open")  # draft | open | paid | cancelled
    source: Mapped[str] = mapped_column(String(200), default="manual")


class PurchaseOrder(BizMixin, Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    items_json: Mapped[list] = mapped_column(JSON)  # [{material_id, material, qty, unit, rate, amount}]
    total: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | sent | received | cancelled
    expected_on: Mapped[date | None] = mapped_column(Date)
    due_on: Mapped[date | None] = mapped_column(Date)  # payment due
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Action(BizMixin, Base):
    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(50))  # purchase_order | invoice | payment_reminder
    summary: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|executed|undone
    approval_code: Mapped[str] = mapped_column(String(10))  # shown only to the human, never returned to the AI
    requested_by_role: Mapped[str] = mapped_column(String(20))
    approved_by_role: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Outbox(BizMixin, Base):
    """Nothing is really sent in this build: 'sending' writes here."""

    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    channel: Mapped[str] = mapped_column(String(20))  # email | whatsapp
    recipient: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | recalled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(BizMixin, Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool: Mapped[str] = mapped_column(String(100))
    args_json: Mapped[dict] = mapped_column(JSON)
    role: Mapped[str] = mapped_column(String(20))
    result_summary: Mapped[str] = mapped_column(Text)
    ok: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Overhead(BizMixin, Base):
    __tablename__ = "overheads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    monthly_amount: Mapped[float] = mapped_column(Float)


class Voucher(BizMixin, Base):
    """Every business event synced from the books (Tally): sales, purchase bills, money in/out,
    goods received. This is the 'what happened' timeline the brief is written from."""

    __tablename__ = "vouchers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vtype: Mapped[str] = mapped_column(String(20))  # sales | purchase | receipt | payment | receipt_note | other
    number: Mapped[str | None] = mapped_column(String(50))
    date: Mapped[date] = mapped_column(Date, index=True)
    party: Mapped[str | None] = mapped_column(String(200))
    amount: Mapped[float] = mapped_column(Float, default=0)
    gst_amount: Mapped[float] = mapped_column(Float, default=0)
    items_json: Mapped[list] = mapped_column(JSON, default=list)  # [{material, qty, unit, rate, amount}]
    ref: Mapped[str | None] = mapped_column(String(100))  # bill reference (invoice no.) or PO-n
    narration: Mapped[str | None] = mapped_column(Text)
    effect: Mapped[str | None] = mapped_column(Text)  # what syncing this voucher changed
    external_key: Mapped[str] = mapped_column(String(300), index=True)
    source: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncFile(BizMixin, Base):
    __tablename__ = "sync_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector: Mapped[str] = mapped_column(String(30))  # tally | excel
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20))  # synced | failed | duplicate
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Brief(BizMixin, Base):
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    for_date: Mapped[date] = mapped_column(Date, index=True)
    headline: Mapped[str] = mapped_column(Text)
    lines_json: Mapped[list] = mapped_column(JSON)
    trigger: Mapped[str] = mapped_column(String(30), default="schedule")  # schedule | manual
    delivered_to: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutomationRun(BizMixin, Base):
    __tablename__ = "automation_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(50), index=True)
    trigger: Mapped[str] = mapped_column(String(30), default="schedule")
    ok: Mapped[bool] = mapped_column(default=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
