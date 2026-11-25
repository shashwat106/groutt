"""Deterministic number formatting (Indian style) so the AI never has to round or convert."""
from __future__ import annotations


def indian_grouping(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts) + "," + tail
    return ("-" if n < 0 else "") + out


def inr(amount: float, short: bool = True) -> str:
    """₹1,23,456 or, when short=True and large, ₹1.23L / ₹1.2Cr."""
    sign = "-" if amount < 0 else ""
    a = abs(amount)
    if short and a >= 1e7:
        return f"{sign}₹{a / 1e7:.2f}Cr"
    if short and a >= 1e5:
        return f"{sign}₹{a / 1e5:.2f}L"
    return f"{sign}₹{indian_grouping(round(a))}"


def qty(x: float, unit: str) -> str:
    return f"{indian_grouping(round(x))} {unit.replace('_', ' ')}"
