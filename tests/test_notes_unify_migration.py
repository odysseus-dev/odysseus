"""Startup migrations that fold legacy note columns into markdown content.

`_migrate_unify_notes` moves the `items` checklist into `- [ ]` task lines and
`_migrate_fold_image_url` moves `image_url` into `![](...)`. Both run on every
boot against the real DB, so the contract is pinned here on a throwaway
file-backed SQLite DB: the fold, idempotency, fence-only content, a malformed
row next to a valid one, the backup table, and the documented rollback SQL.
"""
import json
import re
import sqlite3

import pytest

import core.database as db
from core.notes_markdown import image_urls, parse_task_lines

COLUMNS = ("content", "items", "image_url", "note_type", "gallery_id")


@pytest.fixture
def notes_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE notes (id TEXT PRIMARY KEY, content TEXT, items TEXT, "
        "note_type TEXT, image_url TEXT, gallery_id TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{path}")

    class Notes:
        def add(self, note_id, **values):
            with sqlite3.connect(path) as c:
                c.execute(
                    f"INSERT INTO notes (id, {', '.join(COLUMNS)}) VALUES (?,?,?,?,?,?)",
                    (note_id, *(values.get(k) for k in COLUMNS)),
                )

        def row(self, note_id):
            with sqlite3.connect(path) as c:
                found = c.execute(f"SELECT {', '.join(COLUMNS)} FROM notes WHERE id=?", (note_id,)).fetchone()
            return dict(zip(COLUMNS, found))

        def backup(self, note_id):
            with sqlite3.connect(path) as c:
                found = c.execute(
                    "SELECT content, items, image_url, note_type, migrated_at FROM note_legacy_fields WHERE note_id=?",
                    (note_id,),
                ).fetchone()
            return dict(zip(("content", "items", "image_url", "note_type", "migrated_at"), found)) if found else None

        def migrate(self):
            db._migrate_unify_notes()
            db._migrate_fold_image_url()
            db._migrate_note_legacy_fields_cleanup()

        def sql(self, script):
            with sqlite3.connect(path) as c:
                c.executescript(script)

    return Notes()


def _rollback_sql():
    """The rollback statement documented next to NOTE_LEGACY_FIELDS_DDL."""
    source = open(db.__file__, encoding="utf-8").read()
    block = source.split("# it always holds the pre-migration state. Rolling back is plain SQL:", 1)[1]
    block = block.split("# That restores", 1)[0]
    return "\n".join(re.sub(r"^#\s?", "", line) for line in block.strip().splitlines())


def test_folds_items_into_content_and_clears_items(notes_db):
    items = json.dumps([{"text": "Milk", "done": False}, {"text": "Bread", "done": True, "indent": 1}])
    notes_db.add("n1", content="Groceries", items=items, note_type="checklist")
    notes_db.migrate()
    row = notes_db.row("n1")
    assert row["content"] == "Groceries\n\n- [ ] Milk\n  - [x] Bread"
    assert row["items"] is None
    assert row["note_type"] == "checklist"  # derived on read, column kept


def test_idempotent_on_rerun(notes_db):
    notes_db.add("n1", content="", items=json.dumps([{"text": "A"}]), image_url="/api/upload/i")
    notes_db.migrate()
    once = notes_db.row("n1")
    first_backup = notes_db.backup("n1")
    notes_db.migrate()
    assert notes_db.row("n1") == once
    assert once["content"] == "- [ ] A\n\n![](/api/upload/i)"
    assert notes_db.backup("n1") == first_backup


def test_items_already_present_as_task_lines_are_not_doubled(notes_db):
    notes_db.add("n1", content="- [ ] A", items=json.dumps([{"text": "A"}, {"text": "B"}]))
    notes_db.migrate()
    assert notes_db.row("n1")["content"] == "- [ ] A\n- [ ] B"


def test_items_kept_when_only_fenced_code_has_tasks(notes_db):
    # RaresKeY on #4076: task-looking lines inside a fence are a code sample,
    # so the real legacy items must still be folded in, not dropped.
    content = "How to add a task:\n```\n- [ ] example only\n```"
    notes_db.add("n1", content=content, items=json.dumps([{"text": "Real one"}]))
    notes_db.migrate()
    row = notes_db.row("n1")
    assert row["content"] == content + "\n\n- [ ] Real one"
    assert row["items"] is None


def test_malformed_row_is_skipped_and_keeps_items_next_to_a_valid_row(notes_db):
    notes_db.add("bad", content="keep me", items="{not json")
    notes_db.add("notalist", content="me too", items='{"text": "x"}')
    notes_db.add("ok", content="x", items=json.dumps([{"text": "B", "indent": "oops"}]))
    notes_db.migrate()  # must not raise
    assert notes_db.row("bad") == {**notes_db.row("bad"), "content": "keep me", "items": "{not json"}
    assert notes_db.row("notalist")["items"] == '{"text": "x"}'
    assert notes_db.backup("bad") is None
    ok = notes_db.row("ok")
    assert ok["content"] == "x\n\n- [ ] B"
    assert ok["items"] is None


def test_malformed_row_is_skipped_with_a_warning_naming_it(notes_db, caplog):
    notes_db.add("bad", content="keep me", items="{not json")
    notes_db.add("ok", content="x", items=json.dumps([{"text": "B"}]))
    with caplog.at_level("WARNING", logger="core.database"):
        notes_db.migrate()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(m.startswith("notes unify migration: skipped note bad: ") for m in warnings), warnings
    assert "notes unify migration: 1 note(s) kept their items for a retry" in warnings
    assert not any(" ok:" in m or m.endswith(" ok") for m in warnings), "the valid row is not reported"
    assert notes_db.row("ok")["content"] == "x\n\n- [ ] B"


def test_fold_drops_blank_items_turns_newlines_into_spaces_and_coerces_done_and_indent(notes_db):
    items = [
        {"text": ""}, {"text": "   "}, "not an item", {"done": True},
        {"text": "two\nlines\r\n here"},
        {"text": "ticked", "checked": True},
        {"text": "deep", "indent": 7},
        {"text": "negative", "indent": -2},
        {"text": "float", "indent": 1.9},
        {"text": "missing"},
        {"text": "done", "done": True},
    ]
    notes_db.add("legacy", content="Body", items=json.dumps(items))
    notes_db.migrate()
    row = notes_db.row("legacy")
    assert row["content"] == (
        "Body\n\n"
        "- [ ] two lines  here\n"
        "- [x] ticked\n"
        "      - [ ] deep\n"
        "- [ ] negative\n"
        "  - [ ] float\n"
        "- [ ] missing\n"
        "- [x] done"
    )
    assert row["items"] is None
    assert json.loads(notes_db.backup("legacy")["items"]) == items, "the backup keeps the raw legacy items"


def test_fold_does_not_double_an_item_whose_text_had_a_newline(notes_db):
    notes_db.add("dup", content="- [ ] two lines here", items=json.dumps([{"text": "two\nlines here"}]))
    notes_db.migrate()
    assert notes_db.row("dup")["content"] == "- [ ] two lines here"


@pytest.mark.parametrize("fence", ["```", "~~~", "````"])
def test_items_after_an_unclosed_fence_are_not_folded_into_the_code(notes_db, fence):
    content = f"Code:\n{fence}python\nprint('hi')"
    notes_db.add("n1", content=content, items=json.dumps([{"text": "after fence"}, {"text": "second", "done": True}]))
    notes_db.migrate()
    row = notes_db.row("n1")
    assert row["content"] == content + f"\n{fence}\n\n- [ ] after fence\n- [x] second"
    assert [(t["text"], t["done"]) for t in parse_task_lines(row["content"])] == [("after fence", False), ("second", True)]


def test_an_image_after_an_unclosed_fence_is_not_folded_into_the_code(notes_db):
    notes_db.add("n1", content="~~~\nraw", image_url="/api/upload/i1")
    notes_db.migrate()
    content = notes_db.row("n1")["content"]
    assert content == "~~~\nraw\n~~~\n\n![](/api/upload/i1)"
    assert image_urls(content) == ["/api/upload/i1"]


def test_fold_keeps_crlf_content_byte_for_byte(notes_db):
    notes_db.add("tasks", content="line1\r\n- [ ] crlf task\r\n", items=json.dumps([{"text": "crlf item"}]))
    notes_db.add("prose", content="title\r\nbody", items=json.dumps([{"text": "x"}]))
    notes_db.migrate()
    assert notes_db.row("tasks")["content"] == "line1\r\n- [ ] crlf task\r\n- [ ] crlf item\r\n"
    assert notes_db.row("prose")["content"] == "title\r\nbody\r\n\r\n- [ ] x"


def test_fold_keeps_item_whitespace(notes_db):
    notes_db.add("n1", content="", items=json.dumps([{"text": "in  ner   gap"}, {"text": "  padded  "}]))
    notes_db.migrate()
    assert notes_db.row("n1")["content"] == "- [ ] in  ner   gap\n- [ ] padded"


def test_fold_keeps_duplicate_items_and_a_done_state_the_content_lacks(notes_db):
    notes_db.add("dups", content="- [ ] same", items=json.dumps([{"text": "same", "done": True}, {"text": "same"}]))
    notes_db.add("mixed", content="Shopping\n- [ ] milk", items=json.dumps([{"text": "milk", "done": True}]))
    notes_db.add("twice", content="", items=json.dumps([{"text": "a"}, {"text": "a"}]))
    notes_db.migrate()
    assert notes_db.row("dups")["content"] == "- [ ] same\n- [x] same"
    assert notes_db.row("mixed")["content"] == "Shopping\n- [ ] milk\n- [x] milk"
    assert notes_db.row("twice")["content"] == "- [ ] a\n- [ ] a"


def test_empty_items_rows_are_left_alone(notes_db):
    notes_db.add("empty", content="note body", items="[]")
    notes_db.add("null", content="plain")
    notes_db.migrate()
    assert notes_db.row("empty")["items"] == "[]"
    assert notes_db.row("null")["content"] == "plain"
    assert notes_db.backup("empty") is None and notes_db.backup("null") is None


def test_image_url_folds_into_content_and_keeps_gallery_link(notes_db):
    notes_db.add("photo", content="Receipt", image_url="/api/upload/r1")
    notes_db.add("drawing", content=None, image_url="/api/upload/d1", note_type="draw", gallery_id="g1")
    notes_db.add("already", content="see ![](/api/upload/a1)", image_url="/api/upload/a1")
    notes_db.migrate()
    assert notes_db.row("photo")["content"] == "Receipt\n\n![](/api/upload/r1)"
    assert notes_db.row("photo")["image_url"] is None
    drawing = notes_db.row("drawing")
    assert drawing["content"] == "![](/api/upload/d1)"
    assert drawing["gallery_id"] == "g1"
    assert drawing["note_type"] == "draw"
    assert notes_db.row("already")["content"] == "see ![](/api/upload/a1)"


def test_backup_row_holds_the_pre_migration_values(notes_db):
    items = json.dumps([{"text": "A", "done": True}])
    notes_db.add("n1", content="Body", items=items, image_url="/api/upload/x", note_type="todo")
    notes_db.migrate()
    backup = notes_db.backup("n1")
    assert backup["content"] == "Body"
    assert backup["items"] == items
    assert backup["image_url"] == "/api/upload/x"
    assert backup["note_type"] == "todo"
    assert backup["migrated_at"]


def test_documented_rollback_sql_restores_the_rows(notes_db):
    items = json.dumps([{"text": "A"}, {"text": "B", "done": True}])
    notes_db.add("n1", content="Body", items=items, image_url="/api/upload/x", note_type="todo")
    notes_db.add("n2", content="untouched")
    before = {nid: notes_db.row(nid) for nid in ("n1", "n2")}
    notes_db.migrate()
    assert notes_db.row("n1") != before["n1"]

    notes_db.sql(_rollback_sql())

    assert {nid: notes_db.row(nid) for nid in ("n1", "n2")} == before
    assert notes_db.backup("n1") is None


def test_missing_database_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'absent.db'}")
    db._migrate_unify_notes()
    db._migrate_fold_image_url()
    assert not (tmp_path / "absent.db").exists()


def test_non_sqlite_database_url_is_a_no_op(tmp_path, monkeypatch):
    # The migrations open the SQLite file named by DATABASE_URL directly. With
    # another backend they must not open, create or touch anything; the rows
    # keep their legacy columns and the readers fold them on the fly
    # (tests/test_notes_markdown_routes.py::test_unmigrated_*).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://odysseus@db.invalid:5432/odysseus")
    opened = []

    def refuse(*args, **kwargs):
        opened.append(args)
        raise AssertionError(f"sqlite3.connect{args} on a non-SQLite DATABASE_URL")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    db._migrate_unify_notes()
    db._migrate_fold_image_url()
    assert opened == []
    assert list(tmp_path.iterdir()) == []


def test_driver_qualified_sqlite_url_migrates(notes_db, monkeypatch):
    # `sqlite+pysqlite:///...` names the same file as `sqlite:///...`; the
    # migrations resolve it through `_sqlite_db_path`, not a string prefix.
    plain = db.DATABASE_URL
    monkeypatch.setattr(db, "DATABASE_URL", plain.replace("sqlite:///", "sqlite+pysqlite:///", 1))
    notes_db.add("n1", content="Body", items=json.dumps([{"text": "A"}]), image_url="/api/upload/i")
    notes_db.migrate()
    row = notes_db.row("n1")
    assert row["content"] == "Body\n\n- [ ] A\n\n![](/api/upload/i)"
    assert row["items"] is None and row["image_url"] is None
    assert notes_db.backup("n1")["items"] == json.dumps([{"text": "A"}])


def test_deleting_a_note_removes_its_backup_row(notes_db):
    notes_db.add("n1", content="Body", items=json.dumps([{"text": "A"}]))
    notes_db.add("n2", content="Other", items=json.dumps([{"text": "B"}]))
    notes_db.migrate()
    assert notes_db.backup("n1") and notes_db.backup("n2")
    notes_db.sql("DELETE FROM notes WHERE id = 'n1'")
    assert notes_db.backup("n1") is None
    assert notes_db.backup("n2") is not None


def test_backup_rows_of_notes_deleted_before_the_trigger_are_dropped(notes_db):
    notes_db.add("n1", content="Body", items=json.dumps([{"text": "A"}]))
    db._migrate_unify_notes()
    notes_db.sql("DELETE FROM notes WHERE id = 'n1'")
    assert notes_db.backup("n1") is not None, "no trigger yet: the row is orphaned"
    notes_db.migrate()
    assert notes_db.backup("n1") is None
    notes_db.migrate()  # idempotent


def test_rolled_back_database_deletes_notes_without_the_trigger(notes_db):
    notes_db.add("n1", content="Body", items=json.dumps([{"text": "A"}]))
    notes_db.migrate()
    notes_db.sql(_rollback_sql())
    notes_db.sql("DROP TABLE note_legacy_fields")
    notes_db.sql("DELETE FROM notes WHERE id = 'n1'")  # must not need the backup table
