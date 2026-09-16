"""Behavior tests for the read-only storage diagnostics report (issue #4889).

Each test builds a real temporary SQLite database and upload directory, then
asserts on the returned report. No network, no wall-clock, no source-text
inspection.
"""
import sqlite3

from src.storage_diagnostics import collect_storage_report


def _make_db(path, rows=(), with_fts=False):
    """Create a minimal chat_messages table (plus optional FTS5 table)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE chat_messages ("
        "id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, meta_data TEXT)"
    )
    for row in rows:
        conn.execute(
            "INSERT INTO chat_messages "
            "(id, session_id, role, content, meta_data) VALUES (?, ?, ?, ?, ?)",
            row,
        )
    if with_fts:
        conn.execute(
            "CREATE VIRTUAL TABLE chat_messages_fts USING fts5("
            "content, message_id UNINDEXED, session_id UNINDEXED, role UNINDEXED)"
        )
    conn.commit()
    conn.close()


def test_report_includes_every_section_and_is_marked_read_only(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    report = collect_storage_report(db_path=str(db), upload_dir=str(uploads))

    for key in ("database", "tables", "chat_messages", "fts", "uploads",
                "suspected_orphans"):
        assert key in report, key
    assert report["read_only"] is True


def test_largest_row_is_the_inline_media_row(tmp_path):
    db = tmp_path / "app.db"
    blob = "data:image/png;base64," + ("A" * 4000)
    _make_db(db, rows=[
        ("m1", "s1", "user", "short", "{}"),
        ("m2", "s1", "user", "please inspect " + blob, "{}"),
    ])

    report = collect_storage_report(db_path=str(db), upload_dir=str(tmp_path / "none"))

    largest = report["chat_messages"]["largest"]
    assert largest[0]["id"] == "m2", largest
    assert largest[0]["inline_media"] == 1
    assert largest[0]["chars"] >= 4000
    assert report["chat_messages"]["inline_media_rows"] == 1


def test_largest_respects_limit_and_orders_descending(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=[(f"m{i}", "s1", "user", "x" * i, "{}") for i in range(1, 6)])

    report = collect_storage_report(
        db_path=str(db), upload_dir=str(tmp_path), largest=2
    )

    assert [row["id"] for row in report["chat_messages"]["largest"]] == ["m5", "m4"]
def test_referenced_upload_is_excluded_from_suspected_orphans(tmp_path):
    db = tmp_path / "app.db"
    referenced = "a" * 32 + ".png"
    orphan = "b" * 32 + ".png"
    _make_db(db, rows=[
        ("m1", "s1", "user",
         f"[Attachment: pic.png | id={referenced} | mime=image/png]", "{}"),
    ])
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / referenced).write_bytes(b"x" * 10)
    (uploads / orphan).write_bytes(b"y" * 20)

    report = collect_storage_report(db_path=str(db), upload_dir=str(uploads))

    orphans = report["suspected_orphans"]
    assert orphans["count"] == 1, orphans
    assert orphans["sample_ids"] == [orphan]
    assert orphans["bytes"] == 20


def test_uploads_summary_counts_every_file_including_the_index(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / ("c" * 32 + ".bin")).write_bytes(b"z" * 5)
    (uploads / "uploads.json").write_text("{}")

    report = collect_storage_report(db_path=str(db), upload_dir=str(uploads))

    assert report["uploads"]["files"] == 2
    assert report["uploads"]["bytes"] == 7
    # The index file is not an upload payload, so it is never an orphan.
    assert report["suspected_orphans"]["count"] == 1


def test_injected_reference_ids_skip_the_scan_and_report_complete(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    kept = "d" * 32 + ".png"
    (uploads / kept).write_bytes(b"1")

    report = collect_storage_report(
        db_path=str(db), upload_dir=str(uploads), referenced_ids={kept}
    )

    assert report["suspected_orphans"]["count"] == 0
    assert report["suspected_orphans"]["reference_scan_complete"] is True
def test_missing_reference_tables_mark_the_scan_incomplete(tmp_path):
    """A partial scan must be surfaced, never presented as authoritative."""
    db = tmp_path / "app.db"
    _make_db(db)  # no documents/notes/calendar tables exist

    report = collect_storage_report(db_path=str(db), upload_dir=str(tmp_path))

    orphans = report["suspected_orphans"]
    assert orphans["reference_scan_complete"] is False
    assert orphans["scan_errors"]


def test_fts_reported_when_present(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=[("m1", "s1", "user", "hello", "{}")], with_fts=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO chat_messages_fts (content, message_id, session_id, role) "
        "VALUES ('hello', 'm1', 's1', 'user')"
    )
    conn.commit()
    conn.close()

    report = collect_storage_report(db_path=str(db), upload_dir=str(tmp_path))

    assert report["fts"]["present"] is True
    assert report["fts"]["rows"] == 1


def test_fts_reported_absent_when_table_missing(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db)

    report = collect_storage_report(db_path=str(db), upload_dir=str(tmp_path))

    assert report["fts"]["present"] is False


def test_missing_database_reports_error_without_raising(tmp_path):
    report = collect_storage_report(
        db_path=str(tmp_path / "absent.db"), upload_dir=str(tmp_path)
    )

    assert "error" in report["database"]
    assert report["suspected_orphans"]["count"] == 0


def test_report_does_not_modify_the_database_file(tmp_path):
    db = tmp_path / "app.db"
    _make_db(db, rows=[("m1", "s1", "user", "hello", "{}")])
    before = db.read_bytes()

    collect_storage_report(db_path=str(db), upload_dir=str(tmp_path))

    assert db.read_bytes() == before