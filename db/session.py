"""Engine/session helpers. DATABASE_URL picks SQLite (default) or Postgres (Supabase)."""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{(ROOT / 'data' / 'industryos.db').as_posix()}"
    if url.startswith("postgres://"):  # Supabase sometimes hands out this scheme
        url = "postgresql://" + url[len("postgres://"):]
    return url


@lru_cache
def _engine_for(url: str) -> Engine:
    if url.startswith("sqlite"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    return _engine_for(database_url())


def init_db(drop: bool = False) -> None:
    eng = get_engine()
    if drop:
        Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)


@contextmanager
def session_scope():
    sess: Session = sessionmaker(bind=get_engine(), expire_on_commit=False)()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def business_id() -> int:
    return int(os.environ.get("INDUSTRYOS_BUSINESS_ID", "1"))
