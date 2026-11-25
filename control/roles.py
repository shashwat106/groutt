"""Roles and the permission matrix.

The role comes from INDUSTRYOS_ROLE for now. That is NOT security: anyone who can edit the
config can set 'owner'. Real auth (per-user tokens / Supabase Auth + row-level security)
replaces this before a second business goes live.
"""
from __future__ import annotations

import functools
import os

from fastmcp.exceptions import ToolError

ASK = "ask"
SIMULATE = "simulate"
BUILD = "build"
ONBOARD = "onboard"
ACT_INVOICE = "act_invoice"
ACT_REMINDER = "act_reminder"
ACT_PO = "act_purchase_order"
APPROVE = "approve"

ROLES = ("owner", "accountant", "manager", "viewer")

PERMISSIONS: dict[str, set[str]] = {
    "viewer": {ASK},
    "manager": {ASK, SIMULATE, BUILD},
    "accountant": {ASK, SIMULATE, BUILD, ONBOARD, ACT_INVOICE, ACT_REMINDER, APPROVE},
    "owner": {ASK, SIMULATE, BUILD, ONBOARD, ACT_INVOICE, ACT_REMINDER, ACT_PO, APPROVE},
}

# Which action types each approving role may approve.
APPROVABLE: dict[str, set[str]] = {
    "owner": {"purchase_order", "invoice", "payment_reminder", "supplier_query"},
    "accountant": {"invoice", "payment_reminder", "supplier_query"},
}


def current_role() -> str:
    role = os.environ.get("INDUSTRYOS_ROLE", "owner").strip().lower()
    if role not in ROLES:
        raise ToolError(f"Unknown role '{role}'. Set INDUSTRYOS_ROLE to one of {', '.join(ROLES)}.")
    return role


def allowed(role: str, category: str) -> bool:
    return category in PERMISSIONS.get(role, set())


def requires(*categories: str):
    """Deny the call unless the current role has every listed permission category."""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            role = current_role()
            missing = [c for c in categories if not allowed(role, c)]
            if missing:
                raise ToolError(
                    f"Permission denied: the '{role}' role cannot use {fn.__name__} "
                    f"(needs {', '.join(missing)}). Ask the owner."
                )
            return await fn(*args, **kwargs)

        wrapper.__industryos_categories__ = categories
        return wrapper

    return deco
