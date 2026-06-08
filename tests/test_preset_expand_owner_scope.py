"""Route-level owner-scope test for POST /api/presets/expand.

`expand_character_prompt` resolves a model endpoint to run its LLM call. It must
scope that lookup to the calling user, otherwise it can resolve another owner's
ModelEndpoint (and its decrypted api_key) in a multi-user deployment. See #2283.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from routes.preset_routes import setup_preset_routes


class _FakeRequest:
    """Minimal stand-in: an async ``json()`` body plus a ``state`` namespace."""

    def __init__(self, body, **state):
        self._body = body
        self.state = SimpleNamespace(**state)

    async def json(self):
        return self._body


def _expand_endpoint():
    router = setup_preset_routes(MagicMock())
    for route in router.routes:
        if getattr(route, "path", "") == "/api/presets/expand" and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("POST /api/presets/expand route not registered")


def _patch_model_pipeline(monkeypatch):
    """Capture the owner passed to _resolve_model and stub the LLM call."""
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://endpoint.local/v1", "test-model", {})

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "  expanded prompt  "

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)
    return seen


def test_expand_scopes_model_resolution_to_cookie_user(monkeypatch):
    seen = _patch_model_pipeline(monkeypatch)
    endpoint = _expand_endpoint()

    req = _FakeRequest({"name": "Pirate", "prompt": "talks like a pirate", "model": "test-model"},
                       current_user="alice")
    result = asyncio.run(endpoint(req))

    assert seen["owner"] == "alice"
    assert seen["spec"] == "test-model"
    assert result == {"success": True, "prompt": "expanded prompt"}


def test_expand_attributes_bearer_token_to_its_owner(monkeypatch):
    # effective_user (not get_current_user) resolves a bearer ody_ caller to the
    # token's real owner instead of the sandbox "api" pseudo-user.
    seen = _patch_model_pipeline(monkeypatch)
    endpoint = _expand_endpoint()

    req = _FakeRequest({"name": "Pirate", "model": ""},
                       current_user="api", api_token=True, api_token_owner="bob")
    asyncio.run(endpoint(req))

    assert seen["owner"] == "bob"


def test_expand_short_circuits_without_input(monkeypatch):
    seen = _patch_model_pipeline(monkeypatch)
    endpoint = _expand_endpoint()

    req = _FakeRequest({}, current_user="alice")
    result = asyncio.run(endpoint(req))

    # Nothing to expand: no model resolution attempted.
    assert result["success"] is False
    assert "owner" not in seen
