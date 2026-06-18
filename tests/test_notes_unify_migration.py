"""Tests for the irreversible notes-unification migration (_migrate_unify_notes).

It runs at startup on every DB: folds the legacy structured `items` checklist
into markdown task lines in `content`, then nulls `items`. Because it can't be
undone, we pin the contract — the fold, idempotency, no double-up when content
already has tasks, malformed items surviving, and note_type/image_url staying
untouched — against a throwaway file-backed SQLite DB.
"""
import json
import os
import sqlite3
import tempfile

import pytest


def _make_notes_db(rows):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        "CREATE TABLE notes (id TEXT PRIMARY KEY, content TEXT, items TEXT, "
        "note_type TEXT, image_url TEXT)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO notes (id, content, items, note_type, image_url) VALUES (?,?,?,?,?)",
            (r["id"], r.get("content"), r.get("items"), r.get("note_type"), r.get("image_url")),
        )
    conn.commit()
    conn.close()
    return tmp.name


def _run_migration(monkeypatch, db_path):
    import core.database as db
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{db_path}")
    db._migrate_unify_notes()


def _fetch(db_path, note_id):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT content, items, note_type, image_url FROM notes WHERE id=?",
            (note_id,),
        ).fetchone()
        return dict(zip(["content", "items", "note_type", "image_url"], row))
    finally:
        conn.close()


def test_folds_items_into_content_and_clears_items(monkeypatch):
    items = json.dumps([{"text": "Milk", "done": False}, {"text": "Bread", "done": True}])
    path = _make_notes_db([{
        "id": "n1", "content": "Groceries", "items": items,
        "note_type": "checklist", "image_url": "/api/upload/x",
    }])
    try:
        _run_migration(monkeypatch, path)
        row = _fetch(path, "n1")
        assert row["content"].startswith("Groceries")
        assert "- [ ] Milk" in row["content"]
        assert "- [x] Bread" in row["content"]
        assert row["items"] is None                 # cleared
        assert row["note_type"] == "checklist"      # intentionally untouched
        assert row["image_url"] == "/api/upload/x"  # intentionally untouched
    finally:
        os.unlink(path)


def test_idempotent_on_rerun(monkeypatch):
    path = _make_notes_db([{
        "id": "n1", "content": "", "items": json.dumps([{"text": "A"}]),
        "note_type": "checklist",
    }])
    try:
        _run_migration(monkeypatch, path)
        once = _fetch(path, "n1")["content"]
        _run_migration(monkeypatch, path)  # items already NULL → no-op
        twice = _fetch(path, "n1")["content"]
        assert once == twice == "- [ ] A"
    finally:
        os.unlink(path)


def test_no_double_up_when_content_already_has_tasks(monkeypatch):
    # Content is authoritative: a row that already carries task lines must not
    # get the legacy items appended again.
    path = _make_notes_db([{
        "id": "n1", "content": "- [ ] A", "items": json.dumps([{"text": "A"}]),
        "note_type": "checklist",
    }])
    try:
        _run_migration(monkeypatch, path)
        row = _fetch(path, "n1")
        assert row["content"] == "- [ ] A"
        assert row["content"].count("- [ ] A") == 1
        assert row["items"] is None
    finally:
        os.unlink(path)


def test_legacy_items_preserved_when_only_fenced_code_has_tasks(monkeypatch):
    # Data-loss regression: if a note's only task-looking lines live inside a
    # fenced code block, has_tasks() must report no real tasks so the legacy
    # `items` are still folded in instead of being dropped when `items` is nulled.
    content = "How to add a task:\n```\n- [ ] example only\n```"
    path = _make_notes_db([{
        "id": "n1", "content": content,
        "items": json.dumps([{"text": "Real one", "done": False}]),
        "note_type": "checklist",
    }])
    try:
        _run_migration(monkeypatch, path)
        row = _fetch(path, "n1")
        assert "- [ ] Real one" in row["content"]  # legacy item NOT dropped
        assert "```" in row["content"]             # code sample preserved
        assert row["items"] is None
    finally:
        os.unlink(path)


def test_survives_malformed_items(monkeypatch):
    # A row with non-JSON items must not abort the batch — it's treated as having
    # no items (content kept, items cleared) and other rows still convert.
    path = _make_notes_db([
        {"id": "bad", "content": "keep me", "items": "{not json", "note_type": "checklist"},
        {"id": "ok", "content": "x", "items": json.dumps([{"text": "B"}]), "note_type": "checklist"},
    ])
    try:
        _run_migration(monkeypatch, path)  # must not raise
        bad = _fetch(path, "bad")
        assert bad["content"] == "keep me"
        assert bad["items"] is None
        assert "- [ ] B" in _fetch(path, "ok")["content"]
    finally:
        os.unlink(path)


def test_skips_empty_and_bracket_items_rows(monkeypatch):
    # Rows whose items are NULL / '' / '[]' are filtered out (nothing to fold);
    # they're left exactly as-is.
    path = _make_notes_db([
        {"id": "empty", "content": "note body", "items": "[]", "note_type": "note"},
        {"id": "null", "content": "plain", "items": None, "note_type": "note"},
    ])
    try:
        _run_migration(monkeypatch, path)
        assert _fetch(path, "empty")["content"] == "note body"
        assert _fetch(path, "null")["content"] == "plain"
    finally:
        os.unlink(path)
