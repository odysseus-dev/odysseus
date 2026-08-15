"""Tests for pins.py — the pinboard (resumable topics with outcomes)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import pins  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate the store DB to a temp file AND make memory_store use it.
    memory_store resolves DB_PATH/STORE_DIR at import time, so patch them
    directly (env vars alone leak into the real store in batch runs)."""
    import memory_store
    db = str(tmp_path / "mem.db")
    monkeypatch.setenv("MEMORY_STORE_DB", db)
    monkeypatch.setenv("MEMORY_MEMORY_DIR", str(tmp_path))
    monkeypatch.setattr(memory_store, "DB_PATH", db)
    monkeypatch.setattr(memory_store, "STORE_DIR", str(tmp_path / "store"))
    memory_store._embed.cache = {}
    old = pins.STORE
    pins.STORE = db
    yield tmp_path
    pins.STORE = old


def test_pin_requires_outcome(env):
    res = pins.pin("topic", "open question", "")
    assert res["pinned"] is False
    assert "outcome" in res["error"]


def test_pin_stores(env):
    res = pins.pin("path D", "what does independence mean",
                   "a shared definition")
    assert res["pinned"] is True
    rows = pins.list_pins()
    assert rows and "path D" in rows[0]["text"]


def test_unpin_resolves(env):
    pins.pin("politics", "continue the exchange", "absorbed understanding")
    res = pins.unpin("politics", "discussed, outcome reached")
    assert res["unpinned"] is True
    assert pins.list_pins() == []  # no open pins remain


def test_unpin_missing(env):
    res = pins.unpin("never pinned", "x")
    assert res["unpinned"] is False


def test_recall_surfaces_open(env):
    pins.pin("politics", "continue", "absorbed understanding",
             context="capitalist society")
    rows = pins.recall("political economy")
    assert rows, "open pin should surface"
    assert "politics" in rows[0]["text"]


def test_list_open_only(env):
    pins.pin("topic A", "q", "outcome a")
    pins.pin("topic B", "q", "outcome b")
    pins.unpin("topic A", "done")
    open_pins = pins.list_pins(open_only=True)
    assert all("topic B" in p["text"] for p in open_pins)
