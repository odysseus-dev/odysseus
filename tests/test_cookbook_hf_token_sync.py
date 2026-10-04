"""Cookbook HF token must survive the client -> server state sync (#6361).

The browser POSTs the cookbook state to /api/cookbook/state. The server
encrypts ``env.hfToken`` there and reports only ``hfTokenConfigured`` back on
GET. A client that strips the token from every POST silently loses it on
reload, so these tests pin both halves of the contract.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import cookbook_routes
from routes.cookbook_helpers import _TOKEN_RE
from src.secret_storage import decrypt

ROOT = Path(__file__).resolve().parents[1]
COOKBOOK_RUNNING = ROOT / "static" / "js" / "cookbookRunning.js"

TOKEN = "hf_abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    state_file = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_file))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(cookbook_routes.setup_cookbook_routes())
    return TestClient(app), state_file


def _state(**env):
    # A non-empty servers list mirrors a hydrated client; the server has an
    # anti-wipe guard for empty ones.
    return {
        "tasks": [],
        "env": {"servers": [{"host": "local", "env": "none"}], **env},
    }


def test_posted_token_is_stored_encrypted_and_reported_as_configured(client):
    http, state_file = client

    res = http.post("/api/cookbook/state", json=_state(hfToken=TOKEN))
    assert res.json()["ok"] is True

    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    stored = on_disk["env"]["hfToken"]
    assert stored != TOKEN
    assert decrypt(stored) == TOKEN

    got = http.get("/api/cookbook/state").json()
    assert got["env"]["hfTokenConfigured"] is True
    assert "hfToken" not in got["env"]
    assert TOKEN not in json.dumps(got)


def test_later_sync_without_token_keeps_the_stored_token(client):
    http, state_file = client
    http.post("/api/cookbook/state", json=_state(hfToken=TOKEN))

    res = http.post("/api/cookbook/state", json=_state())
    assert res.json()["ok"] is True

    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert decrypt(on_disk["env"]["hfToken"]) == TOKEN
    assert http.get("/api/cookbook/state").json()["env"]["hfTokenConfigured"] is True


def test_invalid_token_fails_the_whole_state_save(client):
    """The endpoint reports this as ok:false with HTTP 200, which is why the
    client has to pre-validate and check the response body."""
    http, state_file = client

    res = http.post("/api/cookbook/state", json=_state(hfToken="not valid!"))

    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert not state_file.exists()


def _js_source() -> str:
    return COOKBOOK_RUNNING.read_text(encoding="utf-8")


def test_client_token_pattern_matches_server_pattern():
    match = re.search(r"const _HF_TOKEN_RE = /(.+)/;", _js_source())
    assert match, "client-side token pattern is missing"
    assert match.group(1) == _TOKEN_RE.pattern


def test_client_sends_token_with_state_sync():
    src = _js_source()
    assert "_stripStateSecrets(state, { hfToken: pendingHfToken })" in src
    assert "if (pendingHfToken) env.hfToken = pendingHfToken;" in src


def test_client_only_marks_token_synced_after_server_confirms_ok():
    src = _js_source()
    confirm = src.index("if (result && result.ok) _lastSyncedHfToken = pendingHfToken;")
    request = src.index("_stripStateSecrets(state, { hfToken: pendingHfToken })")
    assert request < confirm
    # Never marked as synced on the optimistic path.
    assert src.count("_lastSyncedHfToken = pendingHfToken") == 1


def test_client_does_not_resend_an_already_synced_token():
    src = _js_source()
    assert "token !== _lastSyncedHfToken" in src
