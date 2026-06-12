"""End-to-end check that the `manage_notes` MCP/agent tool supports every notes
feature: unified markdown content, `- [ ]` task lines, embedded `![](url)`
images, list-with-indices, toggle-by-index, and the legacy checklist_items fold.

Drives the REAL tool implementation (`do_manage_notes`) against a throwaway
file-backed SQLite DB. We bind a private engine + sessionmaker on a temp file
and monkeypatch `core.database.SessionLocal`, because `do_manage_notes` does
`from core.database import SessionLocal, Note` inside the function on every call,
so the patched session is picked up. (A `:memory:` DB can't be used here: each
connection gets its own empty database, so init's tables wouldn't be visible to
the tool's own session.)
"""
import json
import asyncio
import tempfile
import os

import pytest


def _make_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import core.database as db

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(
        f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False}
    )
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    return tmp.name, db


def test_manage_notes_covers_every_feature(monkeypatch):
    db_path, db = _make_db(monkeypatch)
    try:
        from src.tool_implementations import do_manage_notes
        Note = db.Note

        def call(**args):
            return asyncio.run(do_manage_notes(json.dumps(args), owner="tester"))

        def fetch(nid):
            s = db.SessionLocal()
            try:
                return s.query(Note).filter(Note.id.startswith(nid)).first()
            finally:
                s.close()

        # 1) add: heading + open/done task lines + an embedded image, all in one
        # markdown body.
        r = call(
            action="add",
            title="Trip",
            content="# Packing\n- [ ] Passport\n- [x] Tickets\n\n![map](https://example.com/map.png)",
        )
        assert "id:" in r.get("response", ""), r
        nid = r["response"].split("id: ")[1].rstrip(")")

        note = fetch(nid)
        assert note.note_type == "note"  # unified type
        assert "![map](https://example.com/map.png)" in note.content  # image kept

        # 2) list: surfaces toggle indices for each task line
        out = call(action="list").get("results", "")
        assert "0: Passport" in out
        assert "[x] 1: Tickets" in out

        # 3) toggle_item: flip index 0 (Passport) to done
        r = call(action="toggle_item", id=nid, index=0)
        assert "marked done" in r.get("response", ""), r
        assert "- [x] Passport" in fetch(nid).content

        # 4) toggle_item out of range → clean error, no crash
        r = call(action="toggle_item", id=nid, index=99)
        assert r.get("error") and "exit_code" in r

        # 4b) due_date (the reminder field) round-trips on add and update. The
        # tool parses natural language / ISO; we only assert it persists and the
        # updated value sticks (exact tz-normalized form is environment-specific).
        r = call(action="add", title="Dentist", content="Cleaning", due_date="2026-07-01T09:00")
        did = r["response"].split("id: ")[1].rstrip(")")
        assert fetch(did).due_date, "due_date should persist on add"
        call(action="update", id=did, due_date="2026-08-15T14:30")
        assert "2026-08" in (fetch(did).due_date or ""), "due_date should update"
        call(action="delete", id=did)

        # 5) checklist_items (legacy) folds into a note that has NO task lines yet.
        r2 = call(action="add", title="Beach", content="What to bring:")
        bid = r2["response"].split("id: ")[1].rstrip(")")
        call(
            action="update",
            id=bid,
            checklist_items=[{"text": "Sunscreen"}, {"text": "Charger", "done": True}],
        )
        c = fetch(bid).content
        assert "- [ ] Sunscreen" in c and "- [x] Charger" in c

        # 5b) Contract: when content already carries task lines, checklist_items is
        # intentionally ignored (content is authoritative — edit it directly).
        before = fetch(nid).content
        call(action="update", id=nid, checklist_items=[{"text": "Ignored"}])
        assert fetch(nid).content == before and "Ignored" not in fetch(nid).content

        # 6) update content directly, including a relative /api/upload image
        call(action="update", id=nid, content="Updated\n![photo](/api/upload/abc123)\n- [ ] New task")
        assert "![photo](/api/upload/abc123)" in fetch(nid).content

        # 7) delete both notes
        assert "Deleted note" in call(action="delete", id=nid).get("response", "")
        assert fetch(nid) is None
        call(action="delete", id=bid)
    finally:
        os.unlink(db_path)
