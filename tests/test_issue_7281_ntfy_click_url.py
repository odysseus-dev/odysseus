import asyncio
import pathlib
import subprocess
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import auth_routes
from routes.note_routes import dispatch_reminder


def _ntfy_integration(base_url="http://93.184.216.34"):
    return [{
        "id": "intg-ntfy-1",
        "preset": "ntfy",
        "enabled": True,
        "base_url": base_url,
        "api_key": "secret-token",
        "auth_type": "bearer",
        "name": "ntfy",
    }]


class _SpyAsyncClient:
    calls = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, **kw):
        _SpyAsyncClient.calls.append((url, kw))
        resp = MagicMock()
        resp.is_success = True
        resp.status_code = 200
        resp.text = "ok"
        return resp


def test_ntfy_reminder_sets_click_url_to_note_when_app_public_url_configured():
    _SpyAsyncClient.calls = []
    settings = {
        "reminder_channel": "ntfy",
        "reminder_llm_synthesis": False,
        "reminder_ntfy_topic": "reminders",
        "app_public_url": "https://odysseus.example.com/",
    }
    with (
        patch("src.integrations.load_integrations", return_value=_ntfy_integration()),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        res = asyncio.run(
            dispatch_reminder(
                "Dentist",
                "Bring insurance card",
                note_id="note_abc123",
                queue_browser=True,
                settings_override=settings,
            )
        )

    assert res["ntfy_sent"] is True
    assert len(_SpyAsyncClient.calls) == 1
    url, kw = _SpyAsyncClient.calls[0]
    assert url == "http://93.184.216.34/reminders"
    assert kw["headers"]["Click"] == "https://odysseus.example.com/#open=notes&note=note_abc123"


def test_ntfy_reminder_sets_root_click_url_when_note_id_missing():
    _SpyAsyncClient.calls = []
    settings = {
        "reminder_channel": "ntfy",
        "reminder_llm_synthesis": False,
        "reminder_ntfy_topic": "reminders",
        "app_public_url": "https://odysseus.example.com",
    }
    with (
        patch("src.integrations.load_integrations", return_value=_ntfy_integration()),
        patch.object(httpx, "AsyncClient", _SpyAsyncClient),
    ):
        res = asyncio.run(
            dispatch_reminder(
                "Standup",
                "Daily sync",
                note_id="",
                queue_browser=True,
                settings_override=settings,
            )
        )

    assert res["ntfy_sent"] is True
    _, kw = _SpyAsyncClient.calls[0]
    assert kw["headers"]["Click"] == "https://odysseus.example.com/"


def test_ntfy_reminder_omits_click_url_when_app_public_url_unset_or_invalid():
    for idx, bad_url in enumerate(("", "   ", "ftp://odysseus.example.com")):
        _SpyAsyncClient.calls = []
        settings = {
            "reminder_channel": "ntfy",
            "reminder_llm_synthesis": False,
            "reminder_ntfy_topic": "reminders",
            "app_public_url": bad_url,
        }
        with (
            patch("src.integrations.load_integrations", return_value=_ntfy_integration()),
            patch.object(httpx, "AsyncClient", _SpyAsyncClient),
        ):
            res = asyncio.run(
                dispatch_reminder(
                    "Ping",
                    "Body",
                    note_id=f"note_omit_{idx}",
                    queue_browser=True,
                    settings_override=settings,
                )
            )
        assert res["ntfy_sent"] is True
        _, kw = _SpyAsyncClient.calls[0]
        assert "Click" not in kw["headers"]


def test_ntfy_connectivity_test_includes_root_click_url_when_app_public_url_set(monkeypatch):
    auth_mgr = MagicMock()
    auth_mgr.is_auth_enabled.return_value = False
    app = FastAPI()
    app.include_router(auth_routes.setup_auth_routes(auth_mgr))
    client = TestClient(app)

    _SpyAsyncClient.calls = []
    monkeypatch.setattr(auth_routes, "get_integration", lambda _id: _ntfy_integration()[0])
    monkeypatch.setattr(
        auth_routes,
        "_load_settings",
        lambda: {
            "reminder_ntfy_topic": "reminders",
            "app_public_url": "https://odysseus.example.com/",
        },
    )
    monkeypatch.setattr(httpx, "AsyncClient", _SpyAsyncClient)

    resp = client.post("/api/auth/integrations/intg-ntfy-1/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(_SpyAsyncClient.calls) == 1
    _, kw = _SpyAsyncClient.calls[0]
    assert kw["headers"]["Click"] == "https://odysseus.example.com/"


def test_init_js_opens_note_from_hash_on_fresh_load():
    init_src = pathlib.Path("static/js/init.js").read_text(encoding="utf-8")
    assert "/^#open=notes(?:&note=([^&]+))?$/" in init_src
    assert "import('./notes.js')" in init_src
    assert "openNote(noteId)" in init_src

    node_script = """
    const assert = require('node:assert');
    const fs = require('node:fs');
    const src = fs.readFileSync('static/js/init.js', 'utf8');
    // Extract the trailing IIFE that handles #open=notes
    const idx = src.lastIndexOf('/* Open deep-linked notes');
    assert(idx !== -1);
    const handlerCode = src.slice(idx);

    let openedId = null;
    let openedPanel = false;
    let replacedUrl = null;
    const listeners = {};
    global.window = {
      location: { hash: '#open=notes&note=note%2042', pathname: '/', search: '' },
      addEventListener: (ev, fn) => { listeners[ev] = fn; },
    };
    global.document = {
      readyState: 'complete',
      addEventListener: () => {},
    };
    global.history = {
      replaceState: (_s, _t, url) => { replacedUrl = url; },
    };

    const transformed = handlerCode.replace(
      "import('./notes.js')",
      "Promise.resolve({ openNote: (id) => { openedId = id; }, openPanel: () => { openedPanel = true; } })"
    );
    (new Function(transformed))();
    setTimeout(() => {
      assert.strictEqual(openedId, 'note 42');
      assert.strictEqual(openedPanel, false);
      assert.strictEqual(replacedUrl, '/');
    }, 10);
    """
    proc = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
