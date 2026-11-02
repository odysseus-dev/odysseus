"""manage_notes reminder-time sniffing must stay linear (CodeQL py/polynomial-redos).

`\\d{1,2}(?::\\d{2})?\\s*(?:am|pm)?\\s+today` let `\\s*` and `\\s+` split one
whitespace run every possible way, so a digit followed by a long whitespace
run in model-supplied note text was O(n^2). The rewrite groups the optional
meridiem with its own whitespace: same language, one way to match.
"""

import json
import sys
import time
import uuid

import pytest

from tests.helpers.import_state import clear_fake_database_modules
from tests.helpers.sqlite_db import make_temp_sqlite

clear_fake_database_modules()

import core.database as cdb

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _bind_temp_db(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.database", cdb)
    parent = sys.modules.get("core")
    if parent is not None:
        monkeypatch.setattr(parent, "database", cdb, raising=False)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    yield


async def _add(content):
    from src.tool_implementations import do_manage_notes

    return await do_manage_notes(
        json.dumps({"action": "add", "title": "remind me", "content": content}),
        owner="tester-" + uuid.uuid4().hex[:6],
    )


async def test_reminder_time_sniffing_whitespace_flood_is_linear():
    started = time.perf_counter()
    res = await _add("remind 1" + "\t" * 40_000 + "x")
    assert time.perf_counter() - started < 2.0
    assert res.get("exit_code", 0) == 0, res


async def test_reminder_time_sniffing_still_finds_time_first_phrases():
    res = await _add("remind me to call mom, 9 pm tomorrow")
    assert res.get("exit_code", 0) == 0, res
    db = _TS()
    try:
        note = db.query(cdb.Note).filter(cdb.Note.id == res["note_id"]).first()
        assert note is not None and "T21:00:00" in (note.due_date or ""), note and note.due_date
    finally:
        db.close()
