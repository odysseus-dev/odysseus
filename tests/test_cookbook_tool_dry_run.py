import asyncio
import json

import httpx

from src.tools.cookbook import do_adopt_served_model, do_serve_preset


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload, *args, **kwargs):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        return _Response(self.payload)


def test_adopt_served_model_dry_run_does_not_touch_tmux():
    result = asyncio.run(do_adopt_served_model(json.dumps({
        "tmux_session": "audit-external",
        "model": "audit/tiny-model",
        "port": 18092,
        "dry_run": True,
    }), owner="alice"))

    assert result["exit_code"] == 0
    assert result["dry_run"] is True
    assert result["tmux_session"] == "audit-external"
    assert "No state was changed" in result["output"]


def test_serve_preset_dry_run_resolves_but_does_not_launch(monkeypatch):
    state = {
        "presets": [{
            "name": "Tiny Local",
            "model": "audit/tiny-model",
            "cmd": "llama-server -m tiny.gguf --port 18092",
            "host": "",
        }]
    }
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _Client(state))

    result = asyncio.run(do_serve_preset(
        '{"name":"Tiny Local","dry_run":true}',
        owner="alice",
    ))

    assert result["exit_code"] == 0
    assert result["dry_run"] is True
    assert result["preset"] == "Tiny Local"
    assert "No server was started" in result["output"]
