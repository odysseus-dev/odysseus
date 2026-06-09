from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from odysseus_desktop_backend.storage import Database
from rpc_server import SidecarApp


def test_rpc_health_and_settings(tmp_path: Path):
    app = SidecarApp(tmp_path)
    try:
        health = app.dispatch("health.ping", {})
        assert health["ok"] is True
        assert health["profile_dir"] == str(tmp_path)

        settings = app.dispatch("settings.set", {"values": {"default_model": "phi3"}})
        assert settings["default_model"] == "phi3"
    finally:
        app.close()

    db = Database(tmp_path)
    assert db.get_setting("default_model") == "phi3"
    db.close()


def test_rpc_session_lifecycle(tmp_path: Path):
    app = SidecarApp(tmp_path)
    try:
        session = app.dispatch("sessions.create", {"title": "Test"})
        assert session["title"] == "Test"

        updated = app.dispatch(
            "sessions.update",
            {"session_id": session["id"], "updates": {"title": "Renamed"}},
        )
        assert updated["title"] == "Renamed"

        listed = app.dispatch("sessions.list", {})
        assert listed[0]["id"] == session["id"]

        deleted = app.dispatch("sessions.delete", {"session_id": session["id"]})
        assert deleted["deleted"] is True
    finally:
        app.close()


def test_rpc_documents_and_rag(tmp_path: Path):
    source = tmp_path / "doc.txt"
    source.write_text(
        "The desktop RAG index stores chunks and cached embeddings in SQLite.",
        encoding="utf-8",
    )
    app = SidecarApp(tmp_path / "profile")
    try:
        imported = app.dispatch("documents.import", {"path": str(source)})
        document_id = imported["document"]["id"]
        assert imported["document"]["index_status"] == "indexed"
        assert imported["index"]["embedded"] == 1

        documents = app.dispatch("documents.list", {})
        assert documents[0]["id"] == document_id

        results = app.dispatch("rag.search", {"query": "cached sqlite embeddings", "limit": 1})
        assert results[0]["document_id"] == document_id

        reindexed = app.dispatch("documents.reindex", {"document_id": document_id})
        assert reindexed["cached"] == 1

        deleted = app.dispatch("documents.delete", {"document_id": document_id})
        assert deleted["deleted"] is True
        assert app.dispatch("rag.search", {"query": "sqlite", "limit": 1}) == []
    finally:
        app.close()


def test_rpc_document_import_errors_are_clear(tmp_path: Path):
    app = SidecarApp(tmp_path / "profile")
    try:
        try:
            app.dispatch("documents.import", {"path": str(tmp_path / "missing.txt")})
            raise AssertionError("missing file import should fail")
        except FileNotFoundError as exc:
            assert "File not found" in str(exc)

        unsupported = tmp_path / "image.png"
        unsupported.write_bytes(b"not a supported document")
        try:
            app.dispatch("documents.import", {"path": str(unsupported)})
            raise AssertionError("unsupported file import should fail")
        except ValueError as exc:
            assert "Unsupported file type" in str(exc)
    finally:
        app.close()


def test_rpc_stdio_process_roundtrip(tmp_path: Path):
    env = os.environ.copy()
    env["ODYSSEUS_PROFILE_DIR"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "rpc_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "health.ping", "params": {}})
        + "\n"
    )
    proc.stdin.flush()
    response = json.loads(proc.stdout.readline())
    assert response["id"] == 1
    assert response["result"]["ok"] is True

    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "app.shutdown", "params": {}})
        + "\n"
    )
    proc.stdin.flush()
    shutdown = json.loads(proc.stdout.readline())
    assert shutdown["result"]["ok"] is True
    assert proc.wait(timeout=5) == 0
