"""Regenerate seed/files/stock_and_parties.xlsx (a messy, owner-style Excel sheet).

Run:  uv run python -m seed.make_files
"""
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "files" / "stock_and_parties.xlsx"


def main() -> Path:
    stock = pd.DataFrame(
        {
            "Item Name": ["Laces", "Insole board", "Toe puff sheet", "Heel counter"],
            "Qty in Hand": [5200, 4100, 900, 1500],
            "UOM": ["pair", "pair", "sheet", "pair"],
            "Rate (Rs)": [12, 30, 85, 9],
            "Min Level": [1000, 1000, 300, 500],
            "Remarks": ["", "new lot", "", "check quality"],
        }
    )
    debtors = pd.DataFrame(
        {
            "Party Name": ["Raj Shoe Mart", "Bhopal Footwear Point"],
            "Mobile No": ["+91 98100 12345", "+91 94250 11122"],
            "Email ID": ["accounts@rajshoemart.example", ""],
            "City": ["Delhi", "Bhopal"],
            "Credit Days": [45, 30],
        }
    )
    OUT.parent.mkdir(exist_ok=True)
    with pd.ExcelWriter(OUT) as xw:
        stock.to_excel(xw, sheet_name="Stock Register", index=False)
        debtors.to_excel(xw, sheet_name="Debtors", index=False)
    return OUT


if __name__ == "__main__":
    print(f"Wrote {main()}")
