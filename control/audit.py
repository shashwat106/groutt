"""@audited: every tool call (allowed, denied or failed) writes one audit_log row."""
from __future__ import annotations

import functools
import inspect
import logging

from fastmcp import Context
from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from db.models import AuditLog
from db.session import business_id, session_scope

log = logging.getLogger("industryos.audit")


def _summarise(result: object) -> str:
    if isinstance(result, BaseModel):
        for attr in ("headline", "summary", "message"):
            val = getattr(result, attr, None)
            if isinstance(val, str) and val:
                return val[:500]
        return result.model_dump_json()[:500]
    return str(result)[:500]


def write_audit(tool: str, args: dict, role: str, summary: str, ok: bool = True) -> None:
    with session_scope() as s:
        s.add(
            AuditLog(
                business_id=business_id(),
                tool=tool,
                args_json=to_jsonable_python(args, fallback=str),
                role=role,
                result_summary=summary,
                ok=ok,
            )
        )
    log.info("audit tool=%s role=%s ok=%s %s", tool, role, ok, summary[:120])


def audited(fn):
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        from control.roles import current_role  # late import avoids cycles

        bound = sig.bind_partial(*args, **kwargs)
        call_args = {k: v for k, v in bound.arguments.items() if not isinstance(v, Context)}
        try:
            role = current_role()
        except Exception:
            role = "unknown"
        try:
            result = await fn(*args, **kwargs)
        except Exception as e:
            write_audit(fn.__name__, call_args, role, f"ERROR: {e}", ok=False)
            raise
        write_audit(fn.__name__, call_args, role, _summarise(result))
        return result

    return wrapper
