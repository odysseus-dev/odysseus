import json
import os
import sqlite3
import sys
import types

import pytest

import src.storage_diagnostics as storage_diagnostics
from src.storage_diagnostics import (
    collect_configured_storage_bloat_diagnostics,
    collect_storage_bloat_diagnostics,
)


def _init_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE chat_messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT
        );
        """
    )
    return conn


def test_missing_database_and_upload_dir_are_non_fatal(tmp_path):
    report = collect_storage_bloat_diagnostics(
        db_path=tmp_path / "missing.db",
        upload_dir=tmp_path / "missing-uploads",
    )

    assert report["status"] == "success"
    assert report["database"]["path_present"] is False
    assert report["uploads"]["present"] is False
    assert "database file missing" in report["warnings"]
    assert "upload directory missing" in report["warnings"]


def test_table_counts_and_missing_fts_warning(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        ("m1", "s1", "user", "hello"),
    )
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)

    assert report["database"]["tables"]["sessions"]["row_count"] == 1
    assert report["database"]["tables"]["chat_messages"]["row_count"] == 1
    assert "size_bytes" in report["database"]["tables"]["chat_messages"]
    assert report["database"]["fts"]["present"] is False
    assert "chat_messages_fts table missing" in report["warnings"]


def test_dbstat_table_size_indicators_when_available(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        ("m1", "s1", "user", "hello"),
    )
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)

    if not report["database"]["table_sizes"]["available"]:
        pytest.skip("SQLite dbstat virtual table is unavailable in this environment")

    table_sizes = report["database"]["table_sizes"]["objects"]
    assert table_sizes["chat_messages"]["size_bytes"] > 0
    assert table_sizes["chat_messages"]["page_count"] > 0
    assert report["database"]["tables"]["chat_messages"]["size_bytes"] > 0


def test_dbstat_table_size_fallback_is_non_fatal(monkeypatch, tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()

    def unavailable(_conn):
        raise sqlite3.OperationalError("no such table: dbstat")

    monkeypatch.setattr(storage_diagnostics, "_dbstat_rows", unavailable)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)

    assert report["status"] == "success"
    assert report["database"]["table_sizes"]["available"] is False
    assert report["database"]["tables"]["chat_messages"]["size_bytes"] is None
    assert "table_size_bytes_unavailable: OperationalError" in report["warnings"]


def test_readonly_connection_quotes_uri_sensitive_database_path(monkeypatch, tmp_path):
    db_path = tmp_path / "app?#%.db"
    conn = _init_db(db_path)
    conn.close()
    captured_uris = []
    original_connect = sqlite3.connect

    def capture_connect(database, *args, **kwargs):
        captured_uris.append(database)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(storage_diagnostics.sqlite3, "connect", capture_connect)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)

    assert report["database"]["path_present"] is True
    assert report["database"]["tables"]["chat_messages"]["row_count"] == 0
    assert captured_uris[0].endswith("/app%3F%23%25.db?mode=ro")
    assert "app?#%.db" not in captured_uris[0]
    assert not any(
        warning.startswith("database diagnostics unavailable")
        for warning in report["warnings"]
    )


def test_fts_present_reports_counts_and_size_when_available(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    try:
        conn.execute("CREATE VIRTUAL TABLE chat_messages_fts USING fts5(content)")
    except sqlite3.OperationalError as exc:
        conn.close()
        pytest.skip(f"SQLite fts5 unavailable: {exc}")
    conn.execute("INSERT INTO chat_messages_fts(rowid, content) VALUES (?, ?)", (1, "hello"))
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)
    fts = report["database"]["fts"]

    assert fts["present"] is True
    assert fts["chat_messages_fts_row_count"] == 1
    assert "chat_messages_fts" in fts["tables"]
    if fts["size_available"]:
        assert fts["size_bytes"] > 0
        assert any(name.startswith("chat_messages_fts") for name in fts["table_sizes"])


def test_largest_rows_report_sizes_without_raw_content(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    secret = "secret-bearing content " + ("x" * 200)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        ("m-large", "s1", "assistant", secret),
    )
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)
    largest = report["database"]["content_rows"]["largest"]

    assert largest[0]["id"] == "m-large"
    assert largest[0]["session_id"] == "s1"
    assert largest[0]["char_length"] == len(secret)
    assert "content" not in largest[0]
    assert "secret-bearing content" not in json.dumps(report)


def test_inline_upload_like_payload_detection_is_metadata_only(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    inline = "data:image/png;base64," + ("A" * 5000)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        ("m-inline", "s1", "user", inline),
    )
    conn.commit()
    conn.close()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=uploads)
    suspects = report["database"]["content_rows"]["inline_payload_suspects"]

    assert suspects[0]["id"] == "m-inline"
    assert "data_url_base64" in suspects[0]["reasons"]
    assert "content" not in suspects[0]
    assert "data:image/png" not in json.dumps(report)


def test_manifest_present_but_unreferenced_upload_is_suspected_orphan(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()
    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "06" / "26"
    dated.mkdir(parents=True)

    kept = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt"
    orphan = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.txt"
    (dated / kept).write_bytes(b"kept")
    (dated / orphan).write_bytes(b"orphan")
    (upload_dir / "uploads.json").write_text(
        json.dumps({"alice:hash": {"id": kept, "size": 4}}),
        encoding="utf-8",
    )

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)
    uploads = report["uploads"]

    assert uploads["file_count"] == 2
    assert uploads["total_size_bytes"] == len(b"kept") + len(b"orphan")
    assert uploads["file_count_observed"] == 2
    assert uploads["total_size_bytes_observed"] == len(b"kept") + len(b"orphan")
    assert uploads["truncated"] is False
    assert uploads["manifest_present"] is True
    assert uploads["manifest_entry_count"] == 1
    assert uploads["manifest_entries"]["sample"] == [
        {"stored_name_hash": storage_diagnostics._hash_upload_identifier(kept)}
    ]
    assert uploads["suspected_orphans"]["count"] == 2
    samples = {
        row["path_hash"]: row
        for row in uploads["suspected_orphans"]["sample"]
    }
    kept_hash = storage_diagnostics._hash_upload_identifier(f"2026/06/26/{kept}")
    orphan_hash = storage_diagnostics._hash_upload_identifier(f"2026/06/26/{orphan}")
    assert set(samples) == {kept_hash, orphan_hash}
    assert samples[kept_hash]["manifest_present"] is True
    assert samples[kept_hash]["live_db_reference_found"] is False
    assert samples[orphan_hash]["manifest_present"] is False
    assert samples[orphan_hash]["relative_dir_bucket"] == "2026/06/26"
    assert samples[orphan_hash]["size_bytes"] == len(b"orphan")
    assert kept not in json.dumps(uploads)
    assert orphan not in json.dumps(uploads)
    assert str(upload_dir) not in json.dumps(uploads)


def test_manifest_present_and_live_db_referenced_upload_is_not_orphan(tmp_path):
    db_path = tmp_path / "app.db"
    live = "cccccccccccccccccccccccccccccccc.txt"
    conn = _init_db(db_path)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content) VALUES (?, ?, ?, ?)",
        ("m1", "s1", "user", f"uploaded file reference {live}"),
    )
    conn.commit()
    conn.close()

    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "06" / "26"
    dated.mkdir(parents=True)
    (dated / live).write_bytes(b"live")
    (upload_dir / "uploads.json").write_text(
        json.dumps({"alice:hash": {"id": live, "size": 4}}),
        encoding="utf-8",
    )

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)
    uploads = report["uploads"]

    assert report["database"]["upload_references"]["live_reference_count"] == 1
    assert uploads["suspected_orphans"]["count"] == 0
    assert uploads["suspected_orphans"]["sample"] == []
    assert live not in json.dumps(uploads)
    assert "uploaded file reference" not in json.dumps(report)


def test_metadata_attachment_reference_is_not_suspected_orphan(tmp_path):
    db_path = tmp_path / "app.db"
    live = "dddddddddddddddddddddddddddddddd.pdf"
    conn = _init_db(db_path)
    conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Session')")
    conn.execute(
        """
        INSERT INTO chat_messages(id, session_id, role, content, metadata)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "m1",
            "s1",
            "user",
            '[{"type":"text","text":"summarize the attachment"}]',
            json.dumps({"attachments": [{"id": live, "type": "application/pdf"}]}),
        ),
    )
    conn.commit()
    conn.close()

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / live).write_bytes(b"live")

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)

    references = report["database"]["upload_references"]
    assert references["live_reference_count"] == 1
    assert "chat_messages.metadata" in references["sources"]
    assert report["uploads"]["suspected_orphans"]["count"] == 0
    assert live not in json.dumps(report)
    assert "summarize the attachment" not in json.dumps(report)


def test_document_pdf_source_reference_is_not_suspected_orphan(tmp_path):
    db_path = tmp_path / "app.db"
    live = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee.pdf"
    conn = _init_db(db_path)
    conn.execute(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            current_content TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO documents(id, current_content) VALUES (?, ?)",
        ("doc1", f'<!-- pdf_source upload_id="{live}" -->\n\n# Private title'),
    )
    conn.commit()
    conn.close()

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / live).write_bytes(b"live")

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)

    references = report["database"]["upload_references"]
    assert "documents.current_content.pdf_source_marker" in references["sources"]
    assert report["uploads"]["suspected_orphans"]["count"] == 0
    assert live not in json.dumps(report)
    assert "Private title" not in json.dumps(report)


def test_upload_traversal_is_bounded_and_reports_truncation(monkeypatch, tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    for index in range(5):
        (upload_dir / f"{index:032x}.txt").write_bytes(b"x")

    monkeypatch.setattr(storage_diagnostics, "MAX_UPLOAD_TRAVERSAL_FILES", 2)
    monkeypatch.setattr(storage_diagnostics, "MAX_UPLOAD_TRAVERSAL_DIRS", 10)
    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)
    uploads = report["uploads"]

    assert uploads["truncated"] is True
    assert uploads["bounds"]["max_visited_files"] == 2
    assert uploads["bounds"]["max_visited_dirs"] == 10
    assert uploads["visited_files"] == 2
    assert uploads["file_count"] is None
    assert uploads["file_count_observed"] == 2
    assert uploads["total_size_bytes"] is None
    assert uploads["total_size_bytes_observed"] == 2
    assert uploads["suspected_orphans"]["observed_count"] == 2
    assert "upload_traversal_truncated" in report["warnings"]


def test_symlinked_upload_file_is_skipped_without_target_metadata(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    outside = tmp_path / "outside-secret-name.bin"
    outside.write_bytes(b"outside-secret-size")
    symlink = upload_dir / "dddddddddddddddddddddddddddddddd.txt"
    try:
        os.symlink(outside, symlink)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)
    uploads = report["uploads"]

    assert uploads["file_count_observed"] == 0
    assert uploads["total_size_bytes_observed"] == 0
    assert uploads["skipped_symlink_count"] == 1
    assert uploads["suspected_orphans"]["sample"] == []
    serialized = json.dumps(report)
    assert "outside-secret-name" not in serialized
    assert symlink.name not in serialized


def test_orphan_sample_does_not_expose_raw_upload_filename(tmp_path):
    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()
    upload_dir = tmp_path / "uploads"
    dated = upload_dir / "2026" / "06" / "26"
    dated.mkdir(parents=True)
    raw_name = "secret-client-filename.pdf"
    (dated / raw_name).write_bytes(b"orphan")

    report = collect_storage_bloat_diagnostics(db_path=db_path, upload_dir=upload_dir)
    sample = report["uploads"]["suspected_orphans"]["sample"][0]

    assert set(sample) == {
        "path_hash",
        "relative_dir_bucket",
        "size_bytes",
        "manifest_present",
        "live_db_reference_found",
    }
    assert sample["path_hash"] == storage_diagnostics._hash_upload_identifier(
        f"2026/06/26/{raw_name}"
    )
    assert sample["relative_dir_bucket"] == "2026/06/26"
    assert sample["size_bytes"] == len(b"orphan")
    assert raw_name not in json.dumps(report["uploads"])


def test_storage_bloat_route_requires_admin_and_returns_report(monkeypatch, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI, HTTPException, Request
    from starlette.testclient import TestClient

    from routes import diagnostics_routes as diag

    def forbidden(_request: Request):
        raise HTTPException(403, "Admin only")

    monkeypatch.setattr(diag, "require_admin", forbidden)
    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(None, False, None, None))
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/diagnostics/storage-bloat").status_code == 403

    db_path = tmp_path / "app.db"
    conn = _init_db(db_path)
    conn.close()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setattr(diag, "require_admin", lambda _request: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(diag, "APP_DB", str(db_path))
    monkeypatch.setattr(diag, "UPLOAD_DIR", str(upload_dir))

    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(None, False, None, None))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/diagnostics/storage-bloat")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["database"]["path_present"] is True


def test_storage_bloat_route_does_not_import_core_database(monkeypatch, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("starlette.testclient")
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from routes import diagnostics_routes as diag

    class PoisonDatabase(types.ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"core.database should not be imported for {name}")

    def fake_collect(*, default_db_path, upload_dir):
        assert default_db_path == str(tmp_path / "app.db")
        assert upload_dir == str(tmp_path / "uploads")
        return {"status": "success", "database": {}, "uploads": {}, "warnings": []}

    monkeypatch.setitem(sys.modules, "core.database", PoisonDatabase("core.database"))
    monkeypatch.setattr(diag, "require_admin", lambda _request: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(diag, "APP_DB", str(tmp_path / "app.db"))
    monkeypatch.setattr(diag, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(diag, "collect_configured_storage_bloat_diagnostics", fake_collect)

    app = FastAPI()
    app.include_router(diag.setup_diagnostics_routes(None, False, None, None))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/diagnostics/storage-bloat")

    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_configured_storage_helper_uses_database_url(monkeypatch, tmp_path):
    default_db = tmp_path / "default.db"
    default_conn = _init_db(default_db)
    default_conn.close()
    custom_db = tmp_path / "custom.db"
    custom_conn = _init_db(custom_db)
    custom_conn.execute("INSERT INTO sessions(id, name) VALUES ('s1', 'Custom')")
    custom_conn.commit()
    custom_conn.close()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{custom_db}")

    report = collect_configured_storage_bloat_diagnostics(
        default_db_path=default_db,
        upload_dir=upload_dir,
    )

    tables = report["database"]["tables"]
    assert tables["sessions"]["row_count"] == 1


@pytest.mark.parametrize(
    ("database_url", "warning"),
    [
        ("sqlite:///:memory:", "in-memory SQLite database has no file size"),
        (
            "postgresql://localhost/odysseus",
            "unsupported database URL; only SQLite file diagnostics are available",
        ),
    ],
)
def test_unsupported_database_sources_are_non_fatal(tmp_path, database_url, warning):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    report = collect_storage_bloat_diagnostics(
        database_url=database_url,
        upload_dir=upload_dir,
    )

    assert report["status"] == "success"
    assert report["database"]["path_present"] is False
    assert warning in report["warnings"]
