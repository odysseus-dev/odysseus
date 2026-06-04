import json
import types

import pytest

import src.ai_interaction as ai_interaction
import src.llm_core as llm_core
from routes.preset_routes import setup_preset_routes


def _expand_handler():
    """Pull the /api/presets/expand endpoint out of the router for direct calls."""
    router = setup_preset_routes(preset_manager=None)
    for route in router.routes:
        if getattr(route, "path", None) == "/api/presets/expand":
            return route.endpoint
    raise AssertionError("expand endpoint not registered")


def _request_with_user(user, body):
    """Minimal Request stand-in: state.current_user plus an awaitable json()."""
    scope = {"type": "http", "headers": [], "state": {}}
    request = types.SimpleNamespace()
    request.state = types.SimpleNamespace(current_user=user)

    async def _json():
        return body

    request.json = _json
    return request


async def test_expand_passes_owner_to_resolve_model(monkeypatch):
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["owner"] = owner
        return ("http://endpoint/v1/chat/completions", "model-x", {})

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "expanded prompt"

    # _resolve_model and llm_call_async are imported at call time inside the
    # handler, so patch them on their source modules, not on routes.preset_routes.
    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm_call_async)

    handler = _expand_handler()
    request = _request_with_user("alice", {"prompt": "a gruff space pirate", "model": "model-x"})

    result = await handler(request)

    assert result["success"] is True
    assert seen["owner"] == "alice"
