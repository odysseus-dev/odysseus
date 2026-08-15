"""Tests for politics.py — the politics wing.

Verifies the absorbed-understanding storage:
- absorb stores a claim+verdict+evidence as a chunk in the politics wing
- list reads it back
- recall finds it (hybrid recall, wing-filtered)
- the wing label is 'politics' so the plugin routes political context there

Isolates to a temp store; never touches the real memory.
"""

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import politics  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate the store DB to a temp file AND make memory_store use it.
    Patches ms.DB_PATH/STORE_DIR directly: memory_store resolves them at import
    time, so env vars alone are not enough once another test has imported it
    (batch runs would otherwise write to the REAL store)."""
    import memory_env
    import memory_store
    db = str(tmp_path / "mem.db")
    monkeypatch.setenv("MEMORY_STORE_DB", db)
    monkeypatch.setenv("MEMORY_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(memory_store, "DB_PATH", db)
    monkeypatch.setattr(memory_store, "STORE_DIR", str(tmp_path / "store"))
    memory_store._embed.cache = {}
    old_store = politics.STORE
    politics.STORE = db
    yield tmp_path
    politics.STORE = old_store


def test_absorb_stores_in_politics_wing(env):
    res = politics.absorb("We live in a capitalist society",
                          "SUBSTANTIATED with refinement",
                          "private ownership dominant; mixed economy caveats",
                          source="test")
    assert res["absorbed"] is True
    assert res["auto_classified"] is True
    assert res["wing"] == "politics"      # auto-assigned, not named
    rows = politics.list_absorbed()
    assert rows, "nothing stored"
    assert "capitalist" in rows[0]["text"]


def test_recall_finds_absorbed(env):
    politics.absorb("We live in a capitalist society",
                    "SUBSTANTIATED with refinement",
                    "private ownership dominant; mixed economy caveats",
                    source="test")
    rows = politics.recall("capitalism economy")
    assert rows, "recall returned nothing"
    assert any("capitalist" in r["text"] for r in rows)


def test_wing_is_politics(env):
    politics.absorb("a test claim", "PARTIAL", "evidence", source="test")
    import memory_store as ms
    db = ms.connect()
    row = db.execute("SELECT wing FROM chunks WHERE wing='politics' "
                     "ORDER BY rowid DESC LIMIT 1").fetchone()
    db.close()
    assert row and row["wing"] == "politics"


def test_recall_ranks_related_first(env):
    """Broad recall (min_sim=0) returns chunks regardless of topic, so the
    meaningful check is RANKING: a capitalism query ranks the capitalist claim
    before another (also-politics) claim. Both chunks are in the politics wing,
    so the query's match quality decides order."""
    politics.absorb("We live in a capitalist society",
                    "SUBSTANTIATED", "private ownership dominant", source="test")
    politics.absorb("Economic inequality is rising in democracies",
                    "SUBSTANTIATED", "income data", source="test")
    rows = politics.recall("capitalism private ownership economic system")
    assert rows, "no chunks recalled"
    assert "capitalist" in rows[0]["text"], \
        f"the capitalist claim must rank first: {rows}"
