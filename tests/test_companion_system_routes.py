"""Route-layer tests for the companion system endpoints.

Helper-level tests (test_companion_system.py) cover the pure logic; these pin
the *HTTP surface* — the admin gate and response behavior — because
/system/db-export is a full-data-export endpoint where authorization and what
ends up in the response body are the security-critical parts.

The harness mounts the real `setup_companion_routes()` router on a bare FastAPI
app and reproduces exactly what AuthMiddleware stamps on request.state for each
caller type, plus an `app.state.auth_manager` stub whose `is_admin` only blesses
the admin user. `require_admin` (core/middleware.py) reads those, so the gate is
exercised for real — no monkeypatching of the gate itself.
"""

import importlib
import os
import sqlite3
import sys
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def db_module():
    """Force the genuine `core.database` into sys.modules for the test.

    Sibling companion test modules (test_companion_pairing/readonly) replace
    `sys.modules['core.database']` with a MagicMock stub at import time so they
    can run under conftest's sqlalchemy stubs. The db-export route reads
    `core.database.DATABASE_URL` at call time, so these tests need the real
    module present and patchable. We swap it in for the duration of the test and
    restore the sibling's stub afterward, keeping the fix order-independent.
    """
    saved = sys.modules.get("core.database")
    sys.modules.pop("core.database", None)
    mod = importlib.import_module("core.database")
    try:
        yield mod
    finally:
        if saved is not None:
            sys.modules["core.database"] = saved


class _AuthMgr:
    """Minimal stand-in for the real AuthManager: configured, and only the
    'admin' username is an admin."""
    is_configured = True

    def is_admin(self, username):
        return username == "admin"


def _make_client(monkeypatch):
    """Build a TestClient over the real companion router. The `X-Test-Auth`
    header selects the caller type and the middleware stamps request.state the
    same way app.py's AuthMiddleware would for that caller."""
    # require_admin short-circuits to allow-all when AUTH_ENABLED=false; keep the
    # gate live for these tests regardless of the ambient environment.
    monkeypatch.setenv("AUTH_ENABLED", "true")

    from companion.routes import setup_companion_routes

    app = FastAPI()
    app.state.auth_manager = _AuthMgr()
    app.state.invalidate_token_cache = lambda: None

    @app.middleware("http")
    async def _fake_auth(request, call_next):
        mode = request.headers.get("x-test-auth", "anon")
        if mode == "admin":
            request.state.current_user = "admin"
            request.state.api_token = False
        elif mode == "user":  # logged-in non-admin cookie session
            request.state.current_user = "alice"
            request.state.api_token = False
        elif mode == "token":  # bearer/API token → sandboxed 'api' pseudo-user
            request.state.current_user = "api"
            request.state.api_token = True
            request.state.api_token_owner = "alice"
            request.state.api_token_scopes = ["companion", "chat"]
        else:  # unauthenticated
            request.state.current_user = None
            request.state.api_token = False
        return await call_next(request)

    app.include_router(setup_companion_routes())
    return TestClient(app)


# ── admin gate ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/companion/system/update-check",
                                  "/api/companion/system/db-export"])
def test_non_admin_session_is_forbidden(monkeypatch, path):
    client = _make_client(monkeypatch)
    assert client.get(path, headers={"X-Test-Auth": "user"}).status_code == 403


@pytest.mark.parametrize("path", ["/api/companion/system/update-check",
                                  "/api/companion/system/db-export"])
def test_bearer_token_is_forbidden(monkeypatch, path):
    """A paired companion/chat token resolves to the 'api' pseudo-user, which is
    never an admin — it must not reach either system route."""
    client = _make_client(monkeypatch)
    assert client.get(path, headers={"X-Test-Auth": "token"}).status_code == 403


@pytest.mark.parametrize("path", ["/api/companion/system/update-check",
                                  "/api/companion/system/db-export"])
def test_anonymous_is_forbidden(monkeypatch, path):
    client = _make_client(monkeypatch)
    assert client.get(path, headers={"X-Test-Auth": "anon"}).status_code == 403


# ── update-check behavior ─────────────────────────────────────────────────────

def test_update_check_admin_success(monkeypatch):
    monkeypatch.setattr(
        "companion.system.fetch_latest_release",
        lambda url, timeout=6.0: {
            "tag": "v9.9.9", "name": "big release",
            "html_url": "https://example/releases/9.9.9",
            "published_at": "2030-01-01T00:00:00Z",
        },
    )
    client = _make_client(monkeypatch)
    r = client.get("/api/companion/system/update-check", headers={"X-Test-Auth": "admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True
    assert body["latest"] == "v9.9.9"
    assert body["update_available"] is True
    assert body["release_url"] == "https://example/releases/9.9.9"


def test_update_check_network_failure_degrades_not_500(monkeypatch):
    def _boom(url, timeout=6.0):
        raise OSError("name resolution failed")

    monkeypatch.setattr("companion.system.fetch_latest_release", _boom)
    client = _make_client(monkeypatch)
    r = client.get("/api/companion/system/update-check", headers={"X-Test-Auth": "admin"})
    assert r.status_code == 200  # not a 500
    body = r.json()
    assert body["reachable"] is False
    assert body["latest"] is None
    assert body["update_available"] is False
    assert "error" in body


# ── db-export behavior ────────────────────────────────────────────────────────

_SQLITE_MAGIC = b"SQLite format 3\x00"


def _seed_data_dir(tmp_path):
    """A realistic data/ layout: a SQLite DB plus a Fernet key file that must
    NEVER ride along in the export."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE api_tokens (id TEXT, token_hash TEXT)")
    conn.execute("INSERT INTO api_tokens VALUES ('t1', 'secret-hash')")
    conn.commit()
    conn.close()
    (data / ".app_key").write_bytes(b"FERNET-KEY-SENTINEL-do-not-leak")
    return db


def test_db_export_admin_returns_sqlite_and_cleans_tmp(monkeypatch, tmp_path, db_module):
    db = _seed_data_dir(tmp_path)
    monkeypatch.setattr(db_module, "DATABASE_URL", f"sqlite:////{db}")

    # Capture the temp snapshot path the route creates so we can assert the
    # background task deleted it after the response.
    created = []
    real_mkstemp = tempfile.mkstemp

    def _spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(tempfile, "mkstemp", _spy_mkstemp)

    client = _make_client(monkeypatch)
    r = client.get("/api/companion/system/db-export", headers={"X-Test-Auth": "admin"})

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment;" in r.headers["content-disposition"]
    assert ".sqlite" in r.headers["content-disposition"]
    # Body is a bare SQLite database, and the seeded row survived the snapshot.
    assert r.content.startswith(_SQLITE_MAGIC)
    assert b"secret-hash" in r.content

    # TestClient runs the response's background task; the temp file is gone.
    assert created, "route did not create a temp snapshot"
    assert not os.path.exists(created[0])


def test_db_export_body_is_only_the_snapshot_no_key_or_archive(monkeypatch, tmp_path, db_module):
    db = _seed_data_dir(tmp_path)
    monkeypatch.setattr(db_module, "DATABASE_URL", f"sqlite:////{db}")

    client = _make_client(monkeypatch)
    r = client.get("/api/companion/system/db-export", headers={"X-Test-Auth": "admin"})

    assert r.status_code == 200
    body = r.content
    # A single SQLite file — not a tar/gzip/zip bundle of the data directory.
    assert body.startswith(_SQLITE_MAGIC)
    assert b"ustar" not in body          # tar
    assert not body.startswith(b"\x1f\x8b")  # gzip
    assert not body.startswith(b"PK\x03\x04")  # zip
    # The Fernet key file's contents must not appear anywhere in the export.
    assert b"FERNET-KEY-SENTINEL-do-not-leak" not in body


def test_db_export_non_sqlite_returns_400(monkeypatch, db_module):
    monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql://u:p@host/db")
    client = _make_client(monkeypatch)
    r = client.get("/api/companion/system/db-export", headers={"X-Test-Auth": "admin"})
    assert r.status_code == 400
