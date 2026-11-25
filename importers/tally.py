"""Tally XML export: ledgers (Sundry Debtors/Creditors), stock items and sales vouchers."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from lxml import etree


def _txt(el, tag: str) -> str | None:
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"-?[\d,]+(\.\d+)?", s)
    return float(m.group(0).replace(",", "")) if m else None


def _unit(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"[\d.,]+\s*/?\s*([A-Za-z_ ]+)$", s.strip())
    return m.group(1).strip() if m else None


VTYPES = {"sales": "sales", "purchase": "purchase", "receipt": "receipt", "payment": "payment",
          "receipt note": "receipt_note", "goods receipt": "receipt_note", "grn": "receipt_note",
          "delivery note": "delivery_note", "credit note": "credit_note", "debit note": "debit_note"}


def _items(v) -> list[dict]:
    out = []
    for e in list(v.iter("ALLINVENTORYENTRIES.LIST")) + list(v.iter("INVENTORYENTRIES.LIST")):
        name = _txt(e, "STOCKITEMNAME")
        if not name:
            continue
        q = _txt(e, "ACTUALQTY") or _txt(e, "BILLEDQTY")
        out.append({"material": name, "qty": abs(_num(q) or 0), "unit": _unit(q) or "",
                    "rate": abs(_num(_txt(e, "RATE")) or 0), "amount": abs(_num(_txt(e, "AMOUNT")) or 0)})
    return out


def read_vouchers(path: str | Path) -> list[dict]:
    """Every voucher in a Tally export (Day Book, vouchers report) as a plain dict."""
    tree = etree.parse(str(path), etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    out = []
    for v in tree.iter("VOUCHER"):
        raw = (_txt(v, "VOUCHERTYPENAME") or v.get("VCHTYPE") or "").strip().lower()
        vtype = VTYPES.get(raw, "other")
        party = _txt(v, "PARTYLEDGERNAME")
        amount = gst = 0.0
        for e in list(v.iter("ALLLEDGERENTRIES.LIST")) + list(v.iter("LEDGERENTRIES.LIST")):
            lname = _txt(e, "LEDGERNAME") or ""
            amt = abs(_num(_txt(e, "AMOUNT")) or 0)
            if party and lname == party:
                amount = max(amount, amt)
            elif "gst" in lname.lower():
                gst += amt
        items = _items(v)
        if not amount:
            amount = sum(i["amount"] for i in items)
        narration = _txt(v, "NARRATION")
        ref = None
        bill = v.find(".//BILLALLOCATIONS.LIST/NAME")
        if bill is not None and bill.text:
            ref = bill.text.strip()
        m = re.search(r"\bPO-?(\d+)\b", narration or "", re.I)
        if m:
            ref = f"PO-{m.group(1)}"
        d = _txt(v, "DATE")
        number = _txt(v, "VOUCHERNUMBER")
        out.append({
            "vtype": vtype, "raw_type": raw, "number": number,
            "date": datetime.strptime(d, "%Y%m%d").date() if d else None,
            "party": party, "amount": round(amount, 2), "gst_amount": round(gst, 2), "items": items,
            "ref": ref, "narration": narration,
            "external_key": f"{vtype}:{number}:{d}:{party}",
        })
    return out


def is_tally_xml(path: str | Path) -> bool:
    p = Path(path)
    if p.suffix.lower() != ".xml":
        return False
    head = p.read_text(encoding="utf-8", errors="ignore")[:4000].upper()
    return "<ENVELOPE" in head and "TALLY" in head


def read_records(path: str | Path) -> tuple[dict[str, list[dict]], list[str]]:
    tree = etree.parse(str(path), etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    out: dict[str, list[dict]] = {"customers": [], "suppliers": [], "materials": [], "invoices": []}
    for led in tree.iter("LEDGER"):
        name = led.get("NAME") or _txt(led, "NAME")
        parent = (_txt(led, "PARENT") or "").lower()
        rec = {
            "name": name,
            "phone": _txt(led, "LEDGERMOBILE") or _txt(led, "LEDGERPHONE"),
            "email": _txt(led, "EMAIL"),
            "city": _txt(led, "LEDSTATENAME") or _txt(led, "STATENAME"),
            "gstin": _txt(led, "PARTYGSTIN"),
        }
        terms = _num(_txt(led, "BILLCREDITPERIOD"))
        if terms is not None:
            rec["payment_terms_days"] = int(terms)
        rec = {k: v for k, v in rec.items() if v is not None}
        if "sundry debtors" in parent:
            out["customers"].append(rec)
        elif "sundry creditors" in parent:
            out["suppliers"].append(rec)
    for item in tree.iter("STOCKITEM"):
        bal = _txt(item, "CLOSINGBALANCE") or _txt(item, "OPENINGBALANCE")
        rate = _txt(item, "CLOSINGRATE") or _txt(item, "OPENINGRATE")
        rec = {
            "name": item.get("NAME") or _txt(item, "NAME"),
            "unit": _txt(item, "BASEUNITS") or _unit(bal),
            "on_hand": _num(bal),
            "unit_cost": _num(rate),
        }
        out["materials"].append({k: v for k, v in rec.items() if v is not None})
    for v in tree.iter("VOUCHER"):
        if (_txt(v, "VOUCHERTYPENAME") or v.get("VCHTYPE") or "").lower() != "sales":
            continue
        party = _txt(v, "PARTYLEDGERNAME")
        amount = gst = 0.0
        for e in list(v.iter("ALLLEDGERENTRIES.LIST")) + list(v.iter("LEDGERENTRIES.LIST")):
            lname = _txt(e, "LEDGERNAME") or ""
            amt = abs(_num(_txt(e, "AMOUNT")) or 0)
            if lname == party:
                amount = max(amount, amt)
            elif "gst" in lname.lower():
                gst += amt
        d = _txt(v, "DATE")
        out["invoices"].append({
            "customer": party,
            "number": _txt(v, "VOUCHERNUMBER"),
            "amount": amount,
            "gst_amount": gst,
            "issued_on": datetime.strptime(d, "%Y%m%d").date() if d else None,
        })
    notes = [f"{Path(path).name}: Tally XML - " + ", ".join(f"{len(r)} {k}" for k, r in out.items() if r)]
    return {k: r for k, r in out.items() if r}, notes
