"""Gap-filling tests for endpoint probing helpers in routes/model_routes.py.

Covers the surfaces not already tested in test_endpoint_probing.py:

  * _probe_endpoint  — Anthropic path (with/without API key, hardcoded fallback),
                       curated-list fallback when /v1/models 404s,
                       401 with API key returns [] immediately.
  * _ping_endpoint   — Ollama port-11434 /api/tags fallback path,
                       5xx on non-Ollama endpoint returns last_error without
                       status_code in the final dict.
  * _probe_single_model — Ollama provider routes to /api/chat with native payload,
                          reasoning models (o1/o3) omit temperature,
                          max_completion_tokens used for o-series models.
  * _classify_endpoint  — RFC-1918 private ranges (10.x, 172.16-31.x, 192.168.x)
                          and loopback variants are classified as "local".
  * _model_endpoint_error_message — Ollama-aware vs generic error text.

HTTP is faked via monkeypatching model_routes.httpx.{get,post} — no network.
"""

import sys
import types
from unittest.mock import MagicMock

import httpx
import pytest

# ── Bootstrap: stub heavy DB imports the same way test_endpoint_probing.py does ──

_endpoint_resolver = sys.modules.get("src.endpoint_resolver")
if _endpoint_resolver is not None and not getattr(_endpoint_resolver, "__file__", None):
    sys.modules.pop("src.endpoint_resolver", None)
    sys.modules.pop("routes.model_routes", None)

if "core.database" not in sys.modules:
    _core_db = types.ModuleType("core.database")
    for _name in [
        "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
        "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
        "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
    ]:
        setattr(_core_db, _name, MagicMock())
    sys.modules["core.database"] = _core_db

import routes.model_routes as model_routes
import src.endpoint_resolver as endpoint_resolver
from routes.model_routes import (
    _probe_endpoint,
    _ping_endpoint,
    _probe_single_model,
    _classify_endpoint,
    _model_endpoint_error_message,
)


def _patch_resolve(monkeypatch):
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)
    monkeypatch.setattr(model_routes, "_normalize_base", lambda url: url.rstrip("/"))


def _resp(status, *, json=None, headers=None, url="https://api.example.com/v1/models"):
    req = httpx.Request("GET", url)
    kwargs = {"request": req}
    if json is not None:
        kwargs["json"] = json
    if headers is not None:
        kwargs["headers"] = headers
    return httpx.Response(status, **kwargs)


# ── _probe_endpoint: Anthropic provider ──────────────────────────────────────

class TestProbeEndpointAnthropic:
    def test_anthropic_with_key_returns_live_model_list(self, monkeypatch):
        """When an API key is provided and /v1/models succeeds, return those models."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(
                200, json={"data": [{"id": "claude-opus-4-7"}, {"id": "claude-sonnet-4-6"}]}
            ),
        )
        result = _probe_endpoint("https://api.anthropic.com/v1", "sk-ant-real")
        assert "claude-opus-4-7" in result
        assert "claude-sonnet-4-6" in result

    def test_anthropic_with_key_on_http_error_returns_empty(self, monkeypatch):
        """API key present + HTTP error → return [] immediately (no hardcoded fallback)."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(401),
        )
        result = _probe_endpoint("https://api.anthropic.com/v1", "bad-key")
        assert result == []

    def test_anthropic_without_key_falls_back_to_hardcoded_list(self, monkeypatch):
        """No API key + HTTP error → fall back to the bundled ANTHROPIC_MODELS list."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(404),
        )
        result = _probe_endpoint("https://api.anthropic.com/v1")
        # The hardcoded list is non-empty and should contain at least one claude model.
        assert len(result) > 0
        assert any("claude" in m for m in result)

    def test_anthropic_without_key_uses_live_list_when_available(self, monkeypatch):
        """No key but /v1/models succeeds → prefer the live list over the hardcoded one."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(
                200, json={"data": [{"id": "claude-future-model"}]}
            ),
        )
        result = _probe_endpoint("https://api.anthropic.com/v1")
        assert result == ["claude-future-model"]


# ── _probe_endpoint: 401 with API key stops immediately ──────────────────────

class TestProbeEndpointAuthFailure:
    def test_401_with_api_key_returns_empty_list(self, monkeypatch):
        """HTTP 401 on a keyed probe means bad credentials — no fallback, return []."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(401),
        )
        result = _probe_endpoint("https://api.openai.com/v1", "bad-key")
        assert result == []

    def test_403_with_api_key_returns_empty_list(self, monkeypatch):
        """HTTP 403 (quota exceeded, wrong plan) — also returns [] for keyed probes."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(403),
        )
        result = _probe_endpoint("https://api.openai.com/v1", "key")
        assert result == []


# ── _probe_endpoint: curated-list fallback ───────────────────────────────────

class TestProbeEndpointCuratedFallback:
    def test_no_curated_match_returns_empty_on_404(self, monkeypatch):
        """Unknown provider with no curated list → empty list when /models 404s."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(404),
        )
        result = _probe_endpoint("https://unknown-provider.example.com/v1")
        assert result == []


# ── _ping_endpoint: Ollama /api/tags fallback ─────────────────────────────────

class TestPingEndpointOllamaTags:
    def test_ollama_port_11434_tries_api_tags_on_4xx(self, monkeypatch):
        """Ollama on port 11434: 4xx on /models → try /api/tags before giving up."""
        _patch_resolve(monkeypatch)
        seen = []

        def fake_get(url, headers=None, timeout=None, **kwargs):
            seen.append(url)
            if url.endswith("/api/tags"):
                return _resp(200)
            return _resp(404)

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("http://localhost:11434/v1")
        assert result["reachable"] is True
        assert any("/api/tags" in u for u in seen)

    def test_ollama_named_host_falls_back_to_api_version(self, monkeypatch):
        """Host containing 'ollama' in name: 5xx on /models → fallback to /api/version."""
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, **kwargs):
            if url.endswith("/api/version"):
                return _resp(200)
            return _resp(500)

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("http://my-ollama-server:11434/v1")
        assert result["reachable"] is True
        assert result["status_code"] == 200

    def test_ollama_all_fallbacks_fail_returns_unreachable(self, monkeypatch):
        """All Ollama paths fail → unreachable with last error preserved."""
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, **kwargs):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("http://localhost:11434/v1")
        assert result["reachable"] is False
        assert result["status_code"] is None
        assert result["error"] is not None


# ── _ping_endpoint: 5xx on non-Ollama endpoint ───────────────────────────────

class TestPingEndpoint5xx:
    def test_5xx_on_non_ollama_returns_last_error(self, monkeypatch):
        """5xx response on a non-Ollama endpoint → unreachable, error carries HTTP status."""
        _patch_resolve(monkeypatch)
        monkeypatch.setattr(
            model_routes.httpx, "get",
            lambda url, headers=None, timeout=None, **kwargs: _resp(503),
        )
        result = _ping_endpoint("https://api.example.com/v1")
        assert result["reachable"] is False
        assert "503" in result["error"]

    def test_read_timeout_returns_last_error(self, monkeypatch):
        """ReadTimeout → unreachable, status_code None, error message present."""
        _patch_resolve(monkeypatch)

        def fake_get(url, headers=None, timeout=None, **kwargs):
            raise httpx.ReadTimeout("timed out")

        monkeypatch.setattr(model_routes.httpx, "get", fake_get)
        result = _ping_endpoint("https://api.example.com/v1")
        assert result["reachable"] is False
        assert result["status_code"] is None
        assert result["error"] is not None


# ── _probe_single_model: Ollama provider ─────────────────────────────────────

class TestProbeSingleModelOllama:
    # _detect_provider returns "ollama" when the path starts with /api/
    # (see _is_ollama_native_url in src/llm_core.py).  Use /api/chat so the
    # base is unmistakably a native-Ollama endpoint.
    _OLLAMA_BASE = "http://localhost:11434/api"

    def test_ollama_routes_to_api_chat(self, monkeypatch):
        """Ollama provider must POST to /api/chat, not /v1/chat/completions."""
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["payload"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"message": {"content": "OK"}}, request=req)

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        result = _probe_single_model(self._OLLAMA_BASE, "", "llama3:8b")
        assert result["status"] == "ok"
        assert "/api/chat" in captured["url"]

    def test_ollama_payload_uses_messages_key(self, monkeypatch):
        """Ollama native payload must have 'messages' and stream=False."""
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["payload"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"message": {"content": "OK"}}, request=req)

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        _probe_single_model(self._OLLAMA_BASE, "", "llama3:8b")
        assert "messages" in captured["payload"]
        assert captured["payload"].get("stream") is False


# ── _probe_single_model: reasoning models omit temperature ───────────────────

class TestProbeSingleModelReasoningModels:
    @pytest.mark.parametrize("model", ["o1-mini", "o3", "o4-mini", "gpt-5"])
    def test_reasoning_model_omits_temperature(self, monkeypatch, model):
        """o1/o3/o4/gpt-5 family models must NOT include temperature in payload."""
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["payload"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
                request=req,
            )

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        _probe_single_model("https://api.openai.com/v1", "key", model)
        assert "temperature" not in captured["payload"], (
            f"Model {model!r} must not receive temperature but payload was: {captured['payload']}"
        )

    @pytest.mark.parametrize("model", ["o1-mini", "o3", "o4-mini"])
    def test_reasoning_model_uses_max_completion_tokens(self, monkeypatch, model):
        """o-series models use max_completion_tokens, not max_tokens."""
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["payload"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
                request=req,
            )

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        _probe_single_model("https://api.openai.com/v1", "key", model)
        assert "max_completion_tokens" in captured["payload"]
        assert "max_tokens" not in captured["payload"]

    def test_regular_model_includes_temperature_and_max_tokens(self, monkeypatch):
        """Non-reasoning models (gpt-4o, etc.) get temperature=0.0 and max_tokens."""
        _patch_resolve(monkeypatch)
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            captured["payload"] = json
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "OK"}}]},
                request=req,
            )

        monkeypatch.setattr(model_routes.httpx, "post", fake_post)
        _probe_single_model("https://api.openai.com/v1", "key", "gpt-4o")
        assert captured["payload"].get("temperature") == 0.0
        assert "max_tokens" in captured["payload"]


# ── _classify_endpoint: RFC-1918 private ranges ──────────────────────────────

class TestClassifyEndpointPrivateRanges:
    @pytest.mark.parametrize("url", [
        "http://192.168.1.100:11434/v1",
        "http://192.168.0.1/v1",
        "http://10.0.0.1:8080/v1",
        "http://10.255.255.254/v1",
        "http://172.16.0.1/v1",
        "http://172.31.255.255/v1",
    ])
    def test_rfc1918_addresses_are_local(self, url):
        assert _classify_endpoint(url) == "local"

    @pytest.mark.parametrize("url", [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8080/v1",
    ])
    def test_loopback_is_local(self, url):
        assert _classify_endpoint(url) == "local"

    @pytest.mark.parametrize("url", [
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://api.groq.com/openai/v1",
    ])
    def test_public_api_hosts_are_api(self, url):
        assert _classify_endpoint(url) == "api"


# ── _model_endpoint_error_message ─────────────────────────────────────────────

class TestModelEndpointErrorMessage:
    def test_ollama_url_returns_ollama_hint(self):
        """Port-11434 endpoint gets Ollama-specific guidance."""
        msg = _model_endpoint_error_message("http://localhost:11434/v1")
        assert "Ollama" in msg
        assert "ollama list" in msg.lower() or "ollama" in msg.lower()

    def test_ollama_url_includes_last_error_when_given(self):
        msg = _model_endpoint_error_message(
            "http://localhost:11434/v1",
            ping={"error": "Connection refused"},
        )
        assert "Connection refused" in msg

    def test_generic_url_returns_generic_message(self):
        msg = _model_endpoint_error_message("https://api.example.com/v1")
        assert "No models found" in msg
        assert "Ollama" not in msg

    def test_generic_url_with_error_includes_error(self):
        msg = _model_endpoint_error_message(
            "https://api.example.com/v1",
            ping={"error": "HTTP 401"},
        )
        assert "HTTP 401" in msg

    def test_no_ping_no_crash(self):
        """Called without a ping dict must not raise."""
        msg = _model_endpoint_error_message("https://api.example.com/v1")
        assert isinstance(msg, str) and len(msg) > 0
