"""Endpoint probing behaviour (REAL routes.model_routes helpers).

ROADMAP "Backend → more tests around endpoint probing and provider setup".
TestSetupProbeSafety in test_model_routes.py already covers the keyed-vs-unkeyed
curated-fallback safety of `_probe_endpoint`. This module pins the rest of the
probe surface that drives endpoint setup and degraded-state reporting:

  * `_probe_endpoint`     — OpenAI vs native-Ollama model-list parsing, the
    /api/tags fallback for Ollama builds without /v1/models, and the
    no-models-found result.
  * `_ping_endpoint`      — reachability classification: 2xx, auth failures,
    the "this is Odysseus, not a model server" /login-redirect trap, generic
    redirects, transport errors, and the native-Ollama /api/version fallback.
  * `_probe_single_model` — ok/fail/timeout status mapping, upstream error-body
    extraction, and per-provider (OpenAI / Anthropic) request routing.
  * `_classify_endpoint`  — the Tailscale CGNAT (100.64.0.0/10) "local" range.

HTTP is faked by monkeypatching `model_routes.httpx.{get,post}`, mirroring the
established pattern in test_model_routes.py — no network, no server.
"""
import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules, preserve_import_state

with preserve_import_state("core.database", "src.database", "core.session_manager", "routes.model_routes"):
    # Match test_model_routes.py: if another test stubbed src.endpoint_resolver
    # during collection, drop the stub so the real URL helpers load here.
    clear_fake_endpoint_resolver_modules()

    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
            "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes
    import src.endpoint_resolver as endpoint_resolver
    from routes.model_routes import (
        _probe_endpoint,
        _ping_endpoint,
        _probe_single_model,
        _resolve_probe_key,
        _classify_endpoint,
        _rewrite_loopback_for_docker,
        _PROVIDER_CURATED,
    )


def _patch_resolve(monkeypatch):
    """Neutralize DNS/Tailscale resolution and base normalization."""
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))


def _resp(status, *, json=None, headers=None, url="https://api.example.com/v1/models"):
    """Build an httpx.Response with a request attached (so raise_for_status works)."""
    req = httpx.Request("GET", url)
    kwargs = {"request": req}
    if json is not None:
        kwargs["json"] = json
    if headers is not None:
        kwargs["headers"] = headers
    return httpx.Response(status, **kwargs)


# ── _probe_endpoint: model-list parsing ──

class TestProbeEndpointParsing:
    def test_parses_openai_data_format(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(
                200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}),
        )
        assert _probe_endpoint("https://api.example.com/v1", "key") == ["gpt-4o", "gpt-4o-mini"]

    def test_parses_ollama_models_format(self, monkeypatch):
        _patch_resolve(monkeypatch)
        # No OpenAI-style "data"; fall back to the native {"models": [...]} shape,
        # honoring both the "name" and "model" keys.
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(
                200, json={"models": [{"name": "llama3:8b"}, {"model": "qwen3:4b"}]}),
        )
        assert _probe_endpoint("https://api.example.com/v1") == ["llama3:8b", "qwen3:4b"]

    def test_falls_back_to_native_ollama_tags(self, monkeypatch):
        _patch_resolve(monkeypatch)
        seen = []

        def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
            seen.append(url)
            if url.endswith("/api/tags"):
                return _resp(200, json={"models": [{"name": "llama3:8b"}]})
            # This Ollama build has no OpenAI-compatible /v1/models surface.
            return _resp(404)

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        assert _probe_endpoint("http://localhost:11434/v1") == ["llama3:8b"]
        assert "http://localhost:11434/v1/models" in seen
        assert "http://localhost:11434/api/tags" in seen

    def test_empty_list_with_no_curation_returns_empty(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(200, json={"data": []}),
        )
        assert _probe_endpoint("https://api.example.com/v1") == []

    def test_chatgpt_subscription_probe_uses_discovery_only(self, monkeypatch):
        _patch_resolve(monkeypatch)
        calls = []

        def fake_fetch(access_token, timeout=5):
            calls.append((access_token, timeout))
            return ["gpt-5.5"]

        monkeypatch.setattr("src.chatgpt_subscription.fetch_available_models", fake_fetch)

        assert _probe_endpoint("https://chatgpt.com/backend-api/codex", "ACCESS", timeout=7) == ["gpt-5.5"]
        assert calls == [("ACCESS", 7)]

    def test_chatgpt_subscription_probe_without_discovery_returns_empty(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr("src.chatgpt_subscription.fetch_available_models", lambda access_token, timeout=5: [])

        assert _probe_endpoint("https://chatgpt.com/backend-api/codex", "ACCESS") == []
        assert _probe_endpoint("https://chatgpt.com/backend-api/codex") == []


# ── _ping_endpoint: reachability classification ──

class TestPingEndpoint:
    def test_reachable_on_2xx(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(200),
        )
        assert _ping_endpoint("https://api.example.com/v1", "key") == {
            "reachable": True, "status_code": 200, "error": None,
        }

    def test_auth_failure_is_reached_but_not_reachable(self, monkeypatch):
        _patch_resolve(monkeypatch)
        # A 401 means the server answered — surface the status, not "offline".
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, verify=None, **kwargs: _resp(401),
        )
        assert _ping_endpoint("https://api.example.com/v1", "bad") == {
            "reachable": False, "status_code": 401, "error": "HTTP 401",
        }

    def test_detects_odysseus_login_redirect(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
            return _resp(302, headers={"location": "/login?next=/"})

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("http://localhost:8080/v1")
        assert result["reachable"] is False
        assert result["status_code"] == 302
        assert "not a model server" in result["error"]

    def test_generic_redirect_reported(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
            return _resp(301, headers={"location": "https://elsewhere.example/"})

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        assert _ping_endpoint("https://api.example.com/v1") == {
            "reachable": False, "status_code": 301, "error": "HTTP 301 redirect",
        }

    def test_transport_error_is_unreachable(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("https://api.example.com/v1")
        assert result["reachable"] is False
        assert result["status_code"] is None
        assert "Connection refused" in result["error"]

    def test_ollama_native_version_fallback(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
            if url.endswith("/api/version"):
                return _resp(200)
            # The OpenAI-compatible /v1/models surface is down on this build.
            return _resp(500)

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        assert _ping_endpoint("http://localhost:11434/v1") == {
            "reachable": True, "status_code": 200, "error": None,
        }


# ── Docker loopback rewrite ──

class TestDockerLoopbackRewrite:
    def test_manual_loopback_rewrites_to_docker_host_when_available(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        monkeypatch.setattr(model_routes, "_container_loopback_reachable", lambda base_url: False)
        assert (
            _rewrite_loopback_for_docker("http://localhost:8000/v1")
            == "http://host.docker.internal:8000/v1"
        )

    def test_reachable_container_loopback_stays_local_even_without_container_flag(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        monkeypatch.setattr(model_routes, "_container_loopback_reachable", lambda base_url: True)
        assert (
            _rewrite_loopback_for_docker("http://127.0.0.1:8001/v1")
            == "http://127.0.0.1:8001/v1"
        )

    def test_cookbook_container_local_loopback_stays_inside_container(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        assert (
            _rewrite_loopback_for_docker("http://localhost:8000/v1", container_local=True)
            == "http://localhost:8000/v1"
        )

    def test_bind_address_becomes_connectable_loopback_for_container_local(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: True)
        assert (
            _rewrite_loopback_for_docker("http://0.0.0.0:8000/v1", container_local=True)
            == "http://127.0.0.1:8000/v1"
        )

    def test_bind_address_becomes_connectable_loopback_on_native_install(self, monkeypatch):
        monkeypatch.setattr(model_routes, "_docker_host_gateway_reachable", lambda: False)
        assert (
            _rewrite_loopback_for_docker("http://0.0.0.0:8000/v1")
            == "http://127.0.0.1:8000/v1"
        )


# ── _probe_single_model: completion probe ──

class TestProbeSingleModel:
    def test_ok_on_success(self, monkeypatch):
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            return _resp(200, json={"choices": [{"message": {"content": "OK"}}]})

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        result = _probe_single_model("https://api.example.com/v1", "key", "gpt-4o")
        assert result["status"] == "ok"
        assert "latency_ms" in result
        assert captured["url"] == "https://api.example.com/v1/chat/completions"

    def test_extracts_dict_error_message(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "post",
            lambda url, headers=None, json=None, timeout=None: _resp(
                400, json={"error": {"message": "model not found"}}),
        )
        result = _probe_single_model("https://api.example.com/v1", "key", "ghost")
        assert result["status"] == "fail"
        assert result["error"] == "model not found"

    def test_extracts_string_error(self, monkeypatch):
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "post",
            lambda url, headers=None, json=None, timeout=None: _resp(
                403, json={"error": "forbidden"}),
        )
        result = _probe_single_model("https://api.example.com/v1", "key", "m")
        assert result["status"] == "fail"
        assert result["error"] == "forbidden"

    def test_timeout(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_post(url, headers=None, json=None, timeout=None):
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        result = _probe_single_model("https://api.example.com/v1", "key", "m", timeout=7)
        assert result["status"] == "timeout"
        assert "7s" in result["error"]

    def test_transport_error_is_fail(self, monkeypatch):
        _patch_resolve(monkeypatch)

        def fake_post(url, headers=None, json=None, timeout=None):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        result = _probe_single_model("https://api.example.com/v1", "key", "m")
        assert result["status"] == "fail"
        assert "refused" in result["error"]

    def test_routes_anthropic_messages_with_x_api_key(self, monkeypatch):
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, payload=json)
            return _resp(200, json={"content": [{"type": "text", "text": "OK"}]})

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        result = _probe_single_model("https://api.anthropic.com/v1", "sk-ant", "claude-sonnet-4-5")
        assert result["status"] == "ok"
        assert captured["url"] == "https://api.anthropic.com/v1/messages"
        assert captured["headers"].get("x-api-key") == "sk-ant"
        assert captured["payload"]["model"] == "claude-sonnet-4-5"

    def test_with_tools_sends_anthropic_tool_schema(self, monkeypatch):
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["payload"] = json
            return _resp(200, json={"content": []})

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        _probe_single_model("https://api.anthropic.com/v1", "sk-ant", "claude-sonnet-4-5", with_tools=True)
        assert "input_schema" in captured["payload"]["tools"][0]

    def test_chatgpt_subscription_skips_completion_probe(self, monkeypatch):
        # This provider speaks the Responses/Codex API. A chat-completions probe
        # would 400 and (via the re-probe flow) hide every model, so it must be
        # short-circuited as discovery-only without any HTTP call.
        _patch_resolve(monkeypatch)

        def boom(*args, **kwargs):
            raise AssertionError("must not send a completion probe for chatgpt-subscription")

        monkeypatch.setattr(model_routes.httpx, "post", boom)
        result = _probe_single_model("https://chatgpt.com/backend-api/codex", None, "gpt-5.1-codex")
        assert result["status"] == "ok"
        assert result.get("skipped") is True
        # Pin the full documented return shape — downstream JSON/UI reads latency_ms.
        assert result["latency_ms"] == 0


# ── _resolve_probe_key: static key vs provider-auth runtime token ──

class TestResolveProbeKey:
    def test_static_endpoint_uses_api_key(self):
        ep = types.SimpleNamespace(id="e1", api_key="sk-static", provider_auth_id=None, owner=None)
        assert _resolve_probe_key(ep) == "sk-static"

    def test_provider_auth_endpoint_resolves_runtime_token(self, monkeypatch):
        ep = types.SimpleNamespace(id="e2", api_key=None, provider_auth_id="auth123", owner="alice")
        seen = {}

        def fake_runtime(endpoint, owner=None):
            seen["owner"] = owner
            return ("https://chatgpt.com/backend-api/codex", "live-bearer")

        monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", fake_runtime)
        assert _resolve_probe_key(ep) == "live-bearer"
        assert seen["owner"] == "alice"

    def test_provider_auth_resolution_failure_returns_none(self, monkeypatch):
        ep = types.SimpleNamespace(id="e3", api_key=None, provider_auth_id="auth123", owner=None)

        def boom(endpoint, owner=None):
            raise RuntimeError("reauth required")

        monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", boom)
        assert _resolve_probe_key(ep) is None


# ── _classify_endpoint: Tailscale CGNAT range ──

class TestClassifyEndpointTailscale:
    @pytest.mark.parametrize("url", [
        "http://100.64.0.1:11434/v1",     # bottom of 100.64.0.0/10
        "http://100.100.50.20:8080/v1",
        "http://100.127.255.254/v1",      # top of the range
    ])
    def test_cgnat_range_is_local(self, url):
        assert _classify_endpoint(url) == "local"

    @pytest.mark.parametrize("url", [
        "http://100.63.255.255/v1",   # just below 100.64.0.0/10
        "http://100.128.0.1/v1",      # just above
        "https://api.openai.com/v1",  # public hostname
    ])
    def test_outside_cgnat_is_api(self, url):
        assert _classify_endpoint(url) == "api"
