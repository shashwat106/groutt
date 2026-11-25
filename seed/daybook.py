"""Builds Tally-style Day Book XML (the same shape Tally Prime exports) for the demo."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

GENERATED = Path(__file__).parent / "files" / "generated"


def _ledger(name: str, amount: float, bill: str | None = None) -> str:
    b = f"<BILLALLOCATIONS.LIST><NAME>{escape(bill)}</NAME><AMOUNT>{amount:.2f}</AMOUNT></BILLALLOCATIONS.LIST>" if bill else ""
    return f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{escape(name)}</LEDGERNAME><AMOUNT>{amount:.2f}</AMOUNT>{b}</ALLLEDGERENTRIES.LIST>"


def _inv(item: str, qty: float, unit: str, rate: float) -> str:
    return (f"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{escape(item)}</STOCKITEMNAME>"
            f"<ACTUALQTY>{qty:g} {unit}</ACTUALQTY><BILLEDQTY>{qty:g} {unit}</BILLEDQTY>"
            f"<RATE>{rate:g}/{unit}</RATE><AMOUNT>{qty * rate:.2f}</AMOUNT></ALLINVENTORYENTRIES.LIST>")


def voucher(vtype: str, d: date, number: str, party: str, *, amount: float = 0, gst: float = 0,
            items: list[tuple] = (), bill: str | None = None, narration: str = "", gst_ledger: str = "Input IGST 5%") -> str:
    entries = [_inv(*i) for i in items]
    if vtype in ("Sales", "Purchase", "Receipt", "Payment"):
        entries.append(_ledger(party, -amount if vtype in ("Sales", "Payment") else amount, bill))
        if gst:
            entries.append(_ledger(gst_ledger if vtype == "Purchase" else "Output IGST 5%", gst))
    return (f'<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="{vtype}" ACTION="Create">'
            f"<DATE>{d:%Y%m%d}</DATE><VOUCHERTYPENAME>{vtype}</VOUCHERTYPENAME><VOUCHERNUMBER>{escape(number)}</VOUCHERNUMBER>"
            f"<PARTYLEDGERNAME>{escape(party)}</PARTYLEDGERNAME><NARRATION>{escape(narration)}</NARRATION>"
            + "".join(entries) + "</VOUCHER></TALLYMESSAGE>")


def envelope(company: str, vouchers: list[str]) -> str:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Tally Day Book export (demo data) -->\n<ENVELOPE>'
            "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC>"
            f"<REPORTNAME>Day Book</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>"
            "</STATICVARIABLES></REQUESTDESC><REQUESTDATA>" + "".join(vouchers) + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")


def todays_daybook(t: date) -> list[str]:
    """What the accountant books in Tally during the demo day."""
    return [
        voucher("Receipt Note", t, "ALT-GRN-81", "Agra Leather Traders", items=[("Leather crust - tan", 410, "sq_ft", 120)],
                narration="Leather for the Oxford order"),
        voucher("Purchase", t, "ALT/1131", "Agra Leather Traders", amount=53812.50, gst=2562.50,
                items=[("Leather crust - tan", 410, "sq_ft", 125)], narration="Bill for leather"),
        voucher("Receipt Note", t, "KSW-GRN-19", "Kanpur Sole Works", items=[("PU sole - Oxford", 1500, "pair", 220)],
                narration="Against PO-1"),
        voucher("Purchase", t, "KSW/552", "Kanpur Sole Works", amount=346500, gst=16500,
                items=[("PU sole - Oxford", 1500, "pair", 220)], narration="Against PO-1"),
        voucher("Receipt", t, "RCPT-0451", "Raj Shoe Mart", amount=120000, bill="INV-0099", narration="NEFT received"),
        voucher("Sales", t, "INV-0109", "Lucknow Footwear Bazaar", amount=210000, gst=10000,
                items=[("Derby Comfort", 200, "pair", 1000)], narration="200 pairs Derby"),
        voucher("Payment", t, "PMT-0098", "Torrent Power", amount=38000, narration="Electricity bill"),
    ]


def write_todays_daybook(t: date, company: str = "Sharma Footwear") -> Path:
    GENERATED.mkdir(parents=True, exist_ok=True)
    for old in GENERATED.glob("tally_daybook_*.xml"):  # keep only the current demo day
        old.unlink()
    path = GENERATED / f"tally_daybook_{t:%Y%m%d}.xml"
    path.write_text(envelope(company, todays_daybook(t)), encoding="utf-8")
    return path
