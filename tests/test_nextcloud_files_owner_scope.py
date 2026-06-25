"""Route-level tests for Nextcloud Files: auth gate, owner isolation, and the
fact that the app password is encrypted at rest and never returned.

Uses dependency_overrides to simulate distinct owners (no full auth middleware),
a temp prefs file so no real user data is touched, and a stubbed client so no
network is involved. Behavioral-first: we hit the routes and assert outcomes.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.prefs_routes as prefs_routes
import routes.nextcloud_routes as nc
from src.auth_helpers import require_user


class _FakeClient:
    """Stand-in for NextcloudClient so route tests never touch the network."""

    def __init__(self, *_, **__):
        pass

    def list_dir(self, path=""):
        return [
            {"name": "Documents", "path": "Documents", "is_dir": True, "size": None,
             "content_type": None, "modified": "2025-06-01T00:00:00+00:00"},
            {"name": "readme.txt", "path": "readme.txt", "is_dir": False, "size": 5,
             "content_type": "text/plain", "modified": "2025-06-01T00:00:00+00:00"},
        ]

    def stat(self, path=""):
        return {"name": path.rsplit("/", 1)[-1] or "/", "path": (path or "").strip("/"),
                "is_dir": False, "size": 5, "content_type": "text/plain", "modified": None}

    def get_file(self, path, max_bytes=None):
        return b"hello", "text/plain"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(tmp_path / "prefs.json"))
    # URL/SSRF validation is exercised in test_nextcloud_client.py; here we
    # accept any URL so account creation never hits real DNS.
    monkeypatch.setattr(nc, "validate_nextcloud_url", lambda url, *a, **k: url)
    a = FastAPI()
    a.include_router(nc.setup_nextcloud_routes())
    return a


def _as(app, owner):
    app.dependency_overrides[nc._require_owner] = lambda: owner


# ── Auth gate ──

def test_routes_require_authentication(app, monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    # No dependency override → real require_user runs; TestClient is not loopback
    # and there is no auth_manager, so an unauthenticated caller is rejected.
    client = TestClient(app)
    r = client.get("/api/nextcloud/accounts")
    assert r.status_code == 401


# ── Owner isolation ──

def test_account_password_is_encrypted_at_rest_and_never_returned(app):
    _as(app, "alice")
    client = TestClient(app)
    r = client.post("/api/nextcloud/accounts", json={
        "label": "Work", "base_url": "https://cloud.example.com",
        "username": "alice", "password": "supersecret",
    })
    assert r.status_code == 200
    body = r.json()
    assert "password" not in body            # never returned
    assert body["configured"] is True
    acc_id = body["id"]

    listed = client.get("/api/nextcloud/accounts").json()["accounts"]
    assert listed[0]["id"] == acc_id
    assert "password" not in listed[0]

    # On disk the password is Fernet-encrypted (enc: prefix), not plaintext.
    raw = json.loads(__import__("pathlib").Path(prefs_routes.PREFS_FILE).read_text())
    stored = raw["_users"]["alice"]["nextcloud_accounts"][0]["password"]
    assert stored.startswith("enc:")
    assert "supersecret" not in stored


def test_owners_are_isolated(app):
    _as(app, "alice")
    alice = TestClient(app)
    created = alice.post("/api/nextcloud/accounts", json={
        "base_url": "https://cloud.example.com", "username": "alice", "password": "pw1",
    }).json()
    alice_id = created["id"]

    # Bob is a different owner: sees none of alice's accounts, can't use her id.
    _as(app, "bob")
    bob = TestClient(app)
    assert bob.get("/api/nextcloud/accounts").json()["accounts"] == []
    assert bob.get(f"/api/nextcloud/list?account={alice_id}&path=").status_code == 404
    assert bob.delete(f"/api/nextcloud/accounts/{alice_id}").status_code == 404


def test_owner_can_delete_their_own_account(app):
    _as(app, "alice")
    client = TestClient(app)
    acc_id = client.post("/api/nextcloud/accounts", json={
        "base_url": "https://cloud.example.com", "username": "alice", "password": "pw",
    }).json()["id"]
    assert client.delete(f"/api/nextcloud/accounts/{acc_id}").status_code == 200
    assert client.get("/api/nextcloud/accounts").json()["accounts"] == []


# ── Browsing (stubbed client) ──

def _make_account(client):
    return client.post("/api/nextcloud/accounts", json={
        "base_url": "https://cloud.example.com", "username": "alice", "password": "pw",
    }).json()["id"]


def test_list_returns_entries(app, monkeypatch):
    _as(app, "alice")
    monkeypatch.setattr(nc, "_client_for", lambda account: _FakeClient())
    client = TestClient(app)
    acc_id = _make_account(client)
    r = client.get(f"/api/nextcloud/list?account={acc_id}&path=")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entries"]}
    assert names == {"Documents", "readme.txt"}


def test_file_download_returns_content_and_type(app, monkeypatch):
    _as(app, "alice")
    monkeypatch.setattr(nc, "_client_for", lambda account: _FakeClient())
    client = TestClient(app)
    acc_id = _make_account(client)
    r = client.get(f"/api/nextcloud/file?account={acc_id}&path=readme.txt")
    assert r.status_code == 200
    assert r.content == b"hello"
    assert r.headers["content-type"].startswith("text/plain")


def test_list_unknown_account_is_404(app):
    _as(app, "alice")
    client = TestClient(app)
    assert client.get("/api/nextcloud/list?account=nope&path=").status_code == 404


# ── Test connection ──

def test_test_connection_requires_fields(app):
    _as(app, "alice")
    client = TestClient(app)
    assert client.post("/api/nextcloud/test", json={}).status_code == 400


def test_test_connection_inline_success(app, monkeypatch):
    _as(app, "alice")
    class _C:
        def __init__(self, base_url, username, password):
            self.base_url = base_url
        def ping(self):
            return True
    monkeypatch.setattr(nc, "NextcloudClient", _C)
    client = TestClient(app)
    r = client.post("/api/nextcloud/test", json={
        "base_url": "https://cloud.example.com", "username": "alice", "password": "tok",
    })
    assert r.status_code == 200 and r.json()["ok"] is True


def test_test_connection_saved_account_failure(app, monkeypatch):
    from src.nextcloud_client import NextcloudError

    _as(app, "alice")
    client = TestClient(app)
    acc_id = _make_account(client)

    class _C:
        def __init__(self, base_url, username, password):
            pass
        def ping(self):
            raise NextcloudError("Nextcloud rejected the credentials (401).", status=401)

    monkeypatch.setattr(nc, "NextcloudClient", _C)
    r = client.post("/api/nextcloud/test", json={"account_id": acc_id})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "credentials" in body["error"]


def test_test_connection_unknown_account_is_404(app, monkeypatch):
    _as(app, "alice")
    # No fake client needed: the owner-scope lookup fails first.
    r = TestClient(app).post("/api/nextcloud/test", json={"account_id": "not-mine"})
    assert r.status_code == 404

