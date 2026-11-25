"""Tally connector: turn Tally exports into Grout data, automatically.

How data arrives (pick what the business has):
  1. Folder sync (works today, no setup): Tally (or the accountant) exports the Day Book as XML into
     inbox/tally/. The scheduler picks new files up every minute. Files are de-duplicated by hash,
     then moved to inbox/tally/processed/.
  2. Live (Tally Prime with its XML port enabled, F1 > Settings > Connectivity): see tally_live.py,
     which asks Tally for the Day Book over HTTP and drops the reply into the same inbox.

What each voucher does when synced:
  sales        -> creates the customer invoice (if new)
  receipt      -> money in: marks the customer's invoice(s) paid (by bill reference, else oldest first)
  receipt_note -> goods received: adds to stock and marks the matching purchase order received
  purchase     -> supplier bill: stored, then checked against the PO and goods received
  payment      -> money out: stored (and counted in today's bank balance)
"""
from __future__ import annotations

import hashlib
import os
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from db.models import Customer, Invoice, Material, PurchaseOrder, Supplier, SyncFile, Voucher
from db.session import ROOT, business_id
from importers import tally

def inbox_dir() -> Path:
    """inbox/tally by default; INDUSTRYOS_INBOX overrides it (tests, or a shared network folder)."""
    return Path(os.environ.get("INDUSTRYOS_INBOX") or ROOT / "inbox" / "tally")


INBOX = inbox_dir()  # default location, for display
OPEN_PO = ("approved", "sent", "partially_received")


def _customer(s: Session, name: str, source: str) -> Customer:
    c = s.query(Customer).filter(Customer.business_id == business_id(), Customer.name.ilike(name)).first()
    if c is None:
        c = Customer(business_id=business_id(), name=name, source=source)
        s.add(c)
        s.flush()
    return c


def _supplier(s: Session, name: str, source: str) -> Supplier:
    sup = s.query(Supplier).filter(Supplier.business_id == business_id(), Supplier.name.ilike(name)).first()
    if sup is None:
        sup = Supplier(business_id=business_id(), name=name, source=source)
        s.add(sup)
        s.flush()
    return sup


def _material(s: Session, name: str, unit: str, source: str) -> Material:
    m = s.query(Material).filter(Material.business_id == business_id(), Material.name.ilike(name)).first()
    if m is None:
        m = Material(business_id=business_id(), name=name, unit=unit or "unit", on_hand=0, source=source)
        s.add(m)
        s.flush()
    return m


def _open_po_line(s: Session, supplier_id: int, material_id: int, ref: str | None):
    """The purchase order line a delivery belongs to: by PO-n reference, else the oldest open PO."""
    q = s.query(PurchaseOrder).filter(PurchaseOrder.business_id == business_id(), PurchaseOrder.supplier_id == supplier_id,
                                      PurchaseOrder.status.in_(OPEN_PO)).order_by(PurchaseOrder.id)
    pos = q.all()
    if ref and ref.upper().startswith("PO-"):
        pos = [p for p in pos if f"PO-{p.id}" == ref.upper()] or pos
    for po in pos:
        for i, line in enumerate(po.items_json):
            if line["material_id"] == material_id and line.get("received", 0) < line["qty"] * 0.98:
                return po, i
    return None, None


def apply_voucher(s: Session, v: dict, source: str) -> str:
    """Apply one voucher's business effect. Returns a one-line description of what changed."""
    bid = business_id()
    vt = v["vtype"]
    if vt == "sales":
        cust = _customer(s, v["party"], source)
        if v["number"] and s.query(Invoice).filter_by(business_id=bid, number=v["number"]).first():
            return f"invoice {v['number']} already known"
        s.add(Invoice(business_id=bid, number=v["number"], customer_id=cust.id, amount=v["amount"], gst_amount=v["gst_amount"],
                      issued_on=v["date"], due_on=v["date"] + timedelta(days=cust.payment_terms_days or 30),
                      status="open", source=source))
        return f"new invoice {v['number']} for {cust.name}"
    if vt == "receipt":
        cust = _customer(s, v["party"], source)
        left = v["amount"]
        invs = s.query(Invoice).filter(Invoice.business_id == bid, Invoice.customer_id == cust.id,
                                       Invoice.status == "open").order_by(Invoice.due_on).all()
        if v.get("ref"):
            invs.sort(key=lambda i: 0 if i.number == v["ref"] else 1)
        touched = []
        for inv in invs:
            if left <= 0:
                break
            due = inv.amount - inv.paid_amount
            pay = min(due, left)
            inv.paid_amount = round(inv.paid_amount + pay, 2)
            left -= pay
            if inv.amount - inv.paid_amount < 1:
                inv.status = "paid"
            touched.append(f"{inv.number or inv.id}{' (fully paid)' if inv.status == 'paid' else ''}")
        return f"payment applied to {', '.join(touched) or 'no open invoice'}" + (f"; Rs {left:,.0f} unallocated" if left > 1 else "")
    if vt == "receipt_note":
        sup = _supplier(s, v["party"], source)
        parts = []
        for it in v["items"]:
            m = _material(s, it["material"], it["unit"], source)
            m.on_hand = round(m.on_hand + it["qty"], 2)
            po, idx = _open_po_line(s, sup.id, m.id, v.get("ref"))
            note = f"+{it['qty']:g} {m.unit} {m.name}"
            if po is not None:
                lines = [dict(l) for l in po.items_json]
                lines[idx]["received"] = round(lines[idx].get("received", 0) + it["qty"], 2)
                po.items_json = lines
                done = all(l.get("received", 0) >= l["qty"] * 0.98 for l in lines)
                po.status = "received" if done else "partially_received"
                note += f" against PO-{po.id} ({po.status.replace('_', ' ')})"
            else:
                note += " (no open PO found)"
            parts.append(note)
        return "stock in: " + "; ".join(parts)
    if vt == "purchase":
        _supplier(s, v["party"], source)
        return f"supplier bill {v['number']} recorded for checking"
    if vt == "payment":
        return f"payment of Rs {v['amount']:,.0f} to {v['party']} recorded"
    return "recorded"


def sync_file(s: Session, path: Path, move: bool = True) -> dict:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    bid = business_id()
    if s.query(SyncFile).filter_by(business_id=bid, sha256=sha, status="synced").first():
        _archive(path, move)
        return {"file": path.name, "status": "duplicate", "vouchers": 0, "changes": []}
    source = f"tally sync {path.name}"
    changes: list[str] = []
    new = 0
    try:
        # Masters first (new parties / stock items), without touching stock levels.
        recs, _ = tally.read_records(path)
        for rec in recs.get("customers", []):
            if rec.get("name"):
                _customer(s, rec["name"], source)
        for rec in recs.get("suppliers", []):
            if rec.get("name"):
                _supplier(s, rec["name"], source)
        for v in tally.read_vouchers(path):
            if v["date"] is None:
                continue
            if s.query(Voucher).filter_by(business_id=bid, external_key=v["external_key"]).first():
                continue
            effect = apply_voucher(s, v, source)
            s.add(Voucher(business_id=bid, vtype=v["vtype"], number=v["number"], date=v["date"], party=v["party"],
                          amount=v["amount"], gst_amount=v["gst_amount"], items_json=v["items"], ref=v["ref"],
                          narration=v["narration"], effect=effect, external_key=v["external_key"], source=source))
            s.flush()
            new += 1
            changes.append(f"{v['vtype'].replace('_', ' ')} {v['number'] or ''} {v['party'] or ''}: {effect}".strip())
        status, summary = "synced", f"{new} new voucher(s)"
    except Exception as e:  # keep the file for a human to look at
        status, summary = "failed", f"could not read: {e}"
    s.add(SyncFile(business_id=bid, connector="tally", filename=path.name, sha256=sha, status=status, summary=summary))
    if status == "synced":
        _archive(path, move)
    return {"file": path.name, "status": status, "vouchers": new, "changes": changes}


def _archive(path: Path, move: bool) -> None:
    if move and path.parent == inbox_dir():
        dest = inbox_dir() / "processed"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest / path.name))


def sync_inbox(s: Session) -> list[dict]:
    inbox_dir().mkdir(parents=True, exist_ok=True)
    return [sync_file(s, p) for p in sorted(inbox_dir().glob("*.xml"))]
