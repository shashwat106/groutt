import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TODAY = "2026-11-23"
os.environ["INDUSTRYOS_TODAY"] = TODAY
os.environ["INDUSTRYOS_OPEN_BROWSER"] = "0"
os.environ["INDUSTRYOS_ROLE"] = "owner"
os.environ["INDUSTRYOS_SCHEDULER"] = "0"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'pytest_import.db').as_posix()}")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh SQLite DB seeded with Sharma Footwear, today pinned to 2026-11-23."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("INDUSTRYOS_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("INDUSTRYOS_ROLE", "owner")
    from seed.sharma_footwear import seed

    return seed()


@pytest.fixture
def mcp_server(db):
    import server

    return server.mcp
