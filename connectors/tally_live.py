"""Live pull from Tally Prime over its built-in XML/HTTP port (default http://localhost:9000).

Enable in Tally Prime: F1 (Help) > Settings > Connectivity > "TallyPrime acts as: Both", port 9000.
This sends Tally's standard "Day Book" export request and saves the reply into inbox/tally/,
where the normal sync picks it up. Not exercised in tests (needs a running Tally).

    uv run python -m connectors.tally_live 2026-11-23            # one day
    uv run python -m connectors.tally_live 2026-11-01 2026-11-23 # a range
"""
from __future__ import annotations

import sys
import urllib.request
from datetime import date

from connectors.tally_sync import inbox_dir

REQUEST = """<ENVELOPE>
 <HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID></HEADER>
 <BODY><DESC><STATICVARIABLES>
  <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
  <SVFROMDATE>{frm}</SVFROMDATE><SVTODATE>{to}</SVTODATE>
 </STATICVARIABLES></DESC></BODY>
</ENVELOPE>"""


def fetch_daybook(frm: date, to: date, url: str = "http://localhost:9000", timeout: int = 30):
    body = REQUEST.format(frm=frm.strftime("%Y%m%d"), to=to.strftime("%Y%m%d")).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "text/xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - local Tally only
        xml = r.read()
    inbox_dir().mkdir(parents=True, exist_ok=True)
    out = inbox_dir() / f"tally_daybook_{frm:%Y%m%d}_{to:%Y%m%d}.xml"
    out.write_bytes(xml)
    return out


if __name__ == "__main__":
    a = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    b = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else a
    print(f"Saved {fetch_daybook(a, b)}")
