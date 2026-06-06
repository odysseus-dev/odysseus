"""manage_calendar list_events should honor common range aliases.

The agent prompt and schema prefer start/end, but model calls can emit
start_date/end_date or from/to. Those aliases used to be ignored, causing the
tool to fall back to its default 14-day window.
"""

import json
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


@pytest.mark.parametrize(
    ("start_key", "end_key"),
    [
        ("start_date", "end_date"),
        ("from", "to"),
        ("range_start", "range_end"),
    ],
)
async def test_list_events_honors_range_aliases(start_key, end_key):
    from src.tool_implementations import do_manage_calendar

    owner = "calendar-alias-" + uuid.uuid4().hex[:8]

    inside = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Late June planning",
        "dtstart": "2126-06-25T10:00:00Z",
    }), owner=owner)
    assert inside.get("exit_code", 0) == 0, inside

    outside = await do_manage_calendar(json.dumps({
        "action": "create_event",
        "summary": "Outside July planning",
        "dtstart": "2126-07-10T10:00:00Z",
    }), owner=owner)
    assert outside.get("exit_code", 0) == 0, outside

    res = await do_manage_calendar(json.dumps({
        "action": "list_events",
        start_key: "2126-06-01T00:00:00Z",
        end_key: "2126-07-01T00:00:00Z",
    }), owner=owner)

    assert res.get("exit_code", 0) == 0, res
    summaries = [event["summary"] for event in res["events"]]
    assert summaries == ["Late June planning"]
    assert "between 2126-06-01 and 2126-07-01" in res["response"]
