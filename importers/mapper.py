"""Guess what a sheet holds and which column is which, from headers + synonyms."""
from __future__ import annotations

import re

SYNONYMS: dict[str, dict[str, list[str]]] = {
    "materials": {
        "name": ["item name", "item", "material", "material name", "stock item", "raw material", "description", "particulars"],
        "on_hand": ["qty in hand", "qty", "quantity", "closing qty", "closing stock", "stock", "balance qty", "on hand", "in stock"],
        "unit": ["unit", "uom", "units", "unit of measure"],
        "unit_cost": ["rate", "cost", "unit cost", "purchase rate", "price", "rate per unit"],
        "reorder_level": ["min level", "reorder level", "minimum stock", "reorder", "min qty", "minimum"],
    },
    "customers": {
        "name": ["party name", "customer", "customer name", "buyer", "buyer name", "name", "party"],
        "phone": ["mobile", "phone", "contact", "whatsapp", "mobile no", "phone no"],
        "email": ["email", "e mail", "email id", "mail"],
        "city": ["city", "station", "location", "place"],
        "gstin": ["gstin", "gst no", "gst number", "gstin uin"],
        "payment_terms_days": ["credit days", "payment terms", "terms", "credit period", "due days"],
    },
    "suppliers": {
        "name": ["supplier", "supplier name", "vendor", "vendor name", "party name", "name", "party"],
        "phone": ["mobile", "phone", "contact", "whatsapp", "mobile no"],
        "email": ["email", "e mail", "email id"],
        "city": ["city", "station", "location"],
        "gstin": ["gstin", "gst no", "gst number"],
        "payment_terms_days": ["credit days", "payment terms", "terms", "credit period"],
        "lead_time_days": ["lead time", "lead days", "delivery days", "lead time days"],
    },
    "products": {
        "name": ["article", "article name", "product", "product name", "style", "model"],
        "selling_price": ["selling price", "mrp", "sale rate", "price", "rate"],
        "unit": ["unit", "uom"],
    },
    "invoices": {
        "customer": ["party name", "customer", "buyer", "party", "customer name"],
        "number": ["invoice no", "bill no", "voucher no", "inv no", "invoice number", "bill number"],
        "amount": ["invoice amount", "amount", "total", "bill amount", "grand total", "net amount"],
        "gst_amount": ["gst", "gst amount", "tax", "tax amount"],
        "issued_on": ["invoice date", "date", "bill date", "voucher date"],
        "due_on": ["due date", "due on"],
        "paid_amount": ["received", "paid", "amount received", "paid amount"],
    },
}

SHEET_HINTS = {
    "materials": ["stock", "material", "inventory", "raw", "store"],
    "customers": ["customer", "debtor", "buyer", "parties sale"],
    "suppliers": ["supplier", "creditor", "vendor"],
    "invoices": ["invoice", "sales", "bill", "receivable", "outstanding"],
    "products": ["product", "article", "style", "price list"],
}

REQUIRED = {
    "materials": ["name"],
    "customers": ["name"],
    "suppliers": ["name"],
    "products": ["name"],
    "invoices": ["customer", "amount"],
}


def norm(h: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def map_columns(headers: list[str], entity: str) -> tuple[dict[str, str], list[str]]:
    """Return ({field: original header}, unmapped headers). Exact synonym match first, then contains."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    normed = {h: norm(h) for h in headers}
    for field, syns in SYNONYMS[entity].items():
        for syn in syns:  # exact
            hit = next((h for h, n in normed.items() if n == syn and h not in used), None)
            if hit:
                mapping[field], _ = hit, used.add(hit)
                break
    for field, syns in SYNONYMS[entity].items():
        if field in mapping:
            continue
        for syn in syns:  # header contains synonym
            hit = next((h for h, n in normed.items() if h not in used and re.search(rf"\b{re.escape(syn)}\b", n)), None)
            if hit:
                mapping[field], _ = hit, used.add(hit)
                break
    return mapping, [h for h in headers if h not in used]


def detect_entity(sheet_name: str, headers: list[str]) -> str | None:
    s = norm(sheet_name)
    for entity, hints in SHEET_HINTS.items():
        if any(h in s for h in hints):
            m, _ = map_columns(headers, entity)
            if all(r in m for r in REQUIRED[entity]):
                return entity
    best, best_score = None, 0
    for entity in SYNONYMS:
        m, _ = map_columns(headers, entity)
        if not all(r in m for r in REQUIRED[entity]):
            continue
        score = len(m)
        if score > best_score:
            best, best_score = entity, score
    return best if best_score >= 2 else None
