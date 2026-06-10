"""Tests for LM Studio provider detection, labeling, and streaming."""
import os
import sys
import types
import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ── Stub heavy optional deps that may not be installed ──
for _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext",
             "sqlalchemy.ext.declarative", "sqlalchemy.ext.hybrid",
             "sqlalchemy.sql", "sqlalchemy.sql.expression",
             "sqlalchemy.sql.sqltypes", "sqlalchemy.types",
             "bcrypt", "pyotp"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

if "src.database" not in sys.modules:
    _db = types.ModuleType("src.database")
    _db.SessionLocal = MagicMock()
    _db.ModelEndpoint = MagicMock()
    sys.modules["src.database"] = _db

from src import llm_core


class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self.is_success = ok

    def json(self):
        return self._payload


# ════════════════════════════════════════════════════════════
# 1. _detect_provider — LM Studio
# ════════════════════════════════════════════════════════════

class TestDetectProviderLmStudio:
    """A local endpoint is LM Studio only when the native-API fingerprint
    confirms it — independent of port, so any/no port works and other servers
    (vLLM, llama.cpp, proxies) are never misdetected."""

    @pytest.mark.parametrize("url", [
        "http://localhost:1234/v1/chat/completions",   # default port
        "http://127.0.0.1:1234/v1/chat/completions",
        "http://192.168.1.10:1234/v1/chat/completions",
        "http://localhost:5000/v1/chat/completions",   # custom port
        "http://my-lm-box:8080/v1/chat/completions",   # Tailscale-style hostname
        "http://localhost:1234",                       # no path
        "http://localhost/v1/chat/completions",        # no explicit port
    ])
    def test_local_host_fingerprint_confirms(self, monkeypatch, url):
        # The fingerprint probe is a network side effect, so callers must opt in
        # with probe=True; only paths that branch on the lmstudio value do.
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        assert llm_core._detect_provider(url, probe=True) == "lmstudio"

    def test_local_non_lmstudio_server_not_misdetected(self, monkeypatch):
        # vLLM / llama.cpp / a proxy: the fingerprint fails, so the result must
        # NOT be lmstudio — otherwise stream_options is silently dropped and
        # token-usage stats break for that server.
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: False)
        assert llm_core._detect_provider("http://localhost:1234/v1/chat/completions", probe=True) == "openai"

    def test_public_host_is_never_fingerprinted(self, monkeypatch):
        # A cloud endpoint must never trigger a probe, even on port 1234.
        def fail(_u):
            raise AssertionError("public host must not be fingerprinted")
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", fail)
        assert llm_core._detect_provider("https://api.example.com:1234/v1/chat/completions", probe=True) == "openai"

    def test_probe_disabled_by_default_skips_fingerprint(self, monkeypatch):
        # Hot paths (header building, reachability, model listing) call
        # _detect_provider without probe=True and must never hit the network.
        def fail(_u):
            raise AssertionError("default detection must not fingerprint")
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", fail)
        assert llm_core._detect_provider("http://localhost:1234/v1/chat/completions") == "openai"


# ════════════════════════════════════════════════════════════
# 2. _detect_provider — other providers still work
# ════════════════════════════════════════════════════════════

class TestDetectProviderOtherProviders:
    @pytest.mark.parametrize("url,expected", [
        ("http://localhost:11434/api/chat", "ollama"),
        ("https://ollama.com/api/chat", "ollama"),
        ("https://api.anthropic.com/v1/messages", "anthropic"),
        ("https://openrouter.ai/api/v1/chat/completions", "openrouter"),
        ("https://api.groq.com/openai/v1/chat/completions", "groq"),
        ("https://api.openai.com/v1/chat/completions", "openai"),
    ])
    def test_provider_detected_correctly(self, url, expected):
        assert llm_core._detect_provider(url) == expected

    def test_empty_string_returns_openai(self):
        assert llm_core._detect_provider("") == "openai"

    def test_none_like_empty_returns_openai(self):
        # _detect_provider coerces None-equivalent empty string
        assert llm_core._detect_provider("") == "openai"


# ════════════════════════════════════════════════════════════
# 3. _provider_label — LM Studio
# ════════════════════════════════════════════════════════════

class TestProviderLabelLmStudio:
    """The label reuses _detect_provider, so it inherits port-independent
    fingerprint detection rather than guessing from the port."""

    def test_localhost_label(self, monkeypatch):
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        assert llm_core._provider_label("http://localhost:1234/v1/chat/completions") == "LM Studio"

    def test_custom_port_label(self, monkeypatch):
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        assert llm_core._provider_label("http://192.168.1.10:5000/v1/chat/completions") == "LM Studio"

    def test_local_non_lmstudio_falls_back_to_local_endpoint(self, monkeypatch):
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: False)
        assert llm_core._provider_label("http://localhost:8000/v1/chat/completions") == "local endpoint"


class TestProviderLabelOtherProviders:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.anthropic.com/v1/messages", "Anthropic"),
        ("https://ollama.com/api/chat", "Ollama Cloud"),
        ("https://api.openai.com/v1/chat/completions", "OpenAI"),
        ("https://openrouter.ai/api/v1/chat/completions", "OpenRouter"),
        ("https://api.groq.com/openai/v1/chat/completions", "Groq"),
    ])
    def test_label_matches_provider(self, url, expected):
        assert llm_core._provider_label(url) == expected


# ════════════════════════════════════════════════════════════
# 4. _is_lmstudio_models_payload — shared shape check
# ════════════════════════════════════════════════════════════

class TestIsLmStudioModelsPayload:
    def test_lmstudio_shape_true(self):
        payload = {"models": [{"key": "qwen3", "architecture": "qwen35"}]}
        assert llm_core._is_lmstudio_models_payload(payload) is True

    @pytest.mark.parametrize("payload", [
        {},
        {"models": []},
        {"models": [{"id": "gpt-4o"}]},                       # OpenAI-compatible shape
        {"models": [{"name": "llama3", "modified_at": "x"}]},  # Ollama /tags shape
        {"data": [{"id": "gpt-4o"}]},                          # no "models" key
    ])
    def test_non_lmstudio_shapes_false(self, payload):
        assert llm_core._is_lmstudio_models_payload(payload) is False


# ════════════════════════════════════════════════════════════
# 5. _is_local_host — fingerprint-probe guard
# ════════════════════════════════════════════════════════════

class TestIsLocalHost:
    @pytest.mark.parametrize("host", [
        "localhost", "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.10",
        "100.64.0.1",            # Tailscale / CGNAT
        "169.254.1.1",           # link-local
        "host.docker.internal",
        "my-mac.local",          # mDNS
        "my-lm-box",             # bare single-label name
    ])
    def test_local_hosts_true(self, host):
        assert llm_core._is_local_host(host) is True

    @pytest.mark.parametrize("host", [
        "api.openai.com", "api.anthropic.com", "8.8.8.8",
        "example.com", "", None,
    ])
    def test_public_or_empty_hosts_false(self, host):
        assert llm_core._is_local_host(host) is False


# ════════════════════════════════════════════════════════════
# 6. _fingerprint_is_lmstudio — cached native-API probe
# ════════════════════════════════════════════════════════════

class TestFingerprintIsLmStudio:
    LMSTUDIO_NATIVE = {"models": [{"key": "qwen3", "architecture": "qwen35"}]}

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        llm_core._lmstudio_models_cache.clear()
        yield
        llm_core._lmstudio_models_cache.clear()

    URL = "http://localhost:5000/v1/chat/completions"

    def test_positive_shape_returns_true(self, monkeypatch):
        captured = {}
        def fake_get(url, timeout=None):
            captured["url"] = url
            return _FakeResponse(self.LMSTUDIO_NATIVE)
        monkeypatch.setattr(llm_core.httpx, "get", fake_get)
        assert llm_core._fingerprint_is_lmstudio(self.URL) is True
        # Probes the same authority the chat request uses (here: the custom port).
        assert captured["url"] == "http://localhost:5000/api/v1/models"

    def test_no_port_probes_scheme_default(self, monkeypatch):
        captured = {}
        def fake_get(url, timeout=None):
            captured["url"] = url
            return _FakeResponse(self.LMSTUDIO_NATIVE)
        monkeypatch.setattr(llm_core.httpx, "get", fake_get)
        assert llm_core._fingerprint_is_lmstudio("http://my-lm-box/v1/chat/completions") is True
        assert captured["url"] == "http://my-lm-box/api/v1/models"

    def test_responding_non_lmstudio_returns_false(self, monkeypatch):
        monkeypatch.setattr(llm_core.httpx, "get",
                            lambda url, timeout=None: _FakeResponse({"data": [{"id": "x"}]}))
        assert llm_core._fingerprint_is_lmstudio(self.URL) is False

    def test_probe_error_returns_false_and_not_cached(self, monkeypatch):
        def boom(url, timeout=None):
            raise OSError("connection refused")
        monkeypatch.setattr(llm_core.httpx, "get", boom)
        assert llm_core._fingerprint_is_lmstudio(self.URL) is False
        # A transient error must not be cached, so the next call re-probes.
        assert ("localhost", 5000) not in llm_core._lmstudio_models_cache

    def test_result_is_cached_within_ttl(self, monkeypatch):
        calls = {"n": 0}

        def counting_get(url, timeout=None):
            calls["n"] += 1
            return _FakeResponse(self.LMSTUDIO_NATIVE)

        monkeypatch.setattr(llm_core.httpx, "get", counting_get)
        assert llm_core._fingerprint_is_lmstudio(self.URL) is True
        assert llm_core._fingerprint_is_lmstudio(self.URL) is True
        assert calls["n"] == 1  # second call served from cache, no re-probe


# ════════════════════════════════════════════════════════════
# 7. stream_llm — stream_options excluded for lmstudio
# ════════════════════════════════════════════════════════════

# Plain class-based fakes (no @asynccontextmanager async generator), matching the
# other streaming tests in this suite (e.g. test_llm_core_streaming.py). The
# stream is driven via asyncio.run so the loop, its async generators, and the
# default executor used by stream_llm's `to_thread` are torn down deterministically.

class _FakeStreamResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"hi"}}]}'
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, captured, kwargs):
        self._captured = captured
        self._kwargs = kwargs

    async def __aenter__(self):
        self._captured["payload"] = self._kwargs.get("json", {})
        return _FakeStreamResp()

    async def __aexit__(self, *exc):
        return False


class _FakeStreamClient:
    is_closed = False

    def __init__(self, captured):
        self._captured = captured

    def stream(self, method, url, **kwargs):
        return _FakeStreamCtx(self._captured, kwargs)


def _run_stream(monkeypatch, url, model, *, is_lmstudio):
    """Drive stream_llm against the fake client and return the captured payload."""
    captured = {}
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeStreamClient(captured))
    monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: is_lmstudio)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        async for _ in llm_core.stream_llm(url, model, [{"role": "user", "content": "hi"}]):
            pass

    asyncio.run(run())
    return captured.get("payload", {})


class TestStreamOptionsExcluded:
    def test_stream_options_absent_for_lmstudio(self, monkeypatch):
        """stream_options must NOT be included in the payload sent to LM Studio."""
        payload = _run_stream(
            monkeypatch, "http://localhost:1234/v1/chat/completions",
            "lmstudio-model", is_lmstudio=True,
        )
        assert "stream_options" not in payload, (
            f"stream_options was unexpectedly present in payload: {payload}"
        )

    def test_stream_options_present_for_openai(self, monkeypatch):
        """stream_options SHOULD be included for OpenAI-compatible endpoints."""
        payload = _run_stream(
            monkeypatch, "http://localhost:8080/v1/chat/completions",
            "some-model", is_lmstudio=False,
        )
        assert "stream_options" in payload, (
            "stream_options should be present for non-excluded providers"
        )


# ════════════════════════════════════════════════════════════
# 8. build_chat_url / build_models_url — LM Studio routing
# ════════════════════════════════════════════════════════════

class TestEndpointResolverLmStudio:
    def test_build_chat_url_lmstudio(self, monkeypatch):
        import src.endpoint_resolver as er
        # Skip DNS / Tailscale resolution and the native-API probe.
        monkeypatch.setattr(er, "resolve_url", lambda url: url)
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        result = er.build_chat_url("http://localhost:1234/v1")
        assert result == "http://localhost:1234/v1/chat/completions"

    def test_build_models_url_lmstudio(self, monkeypatch):
        import src.endpoint_resolver as er
        monkeypatch.setattr(er, "resolve_url", lambda url: url)
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        result = er.build_models_url("http://localhost:1234/v1")
        assert result == "http://localhost:1234/v1/models"

    def test_build_chat_url_lan_lmstudio(self, monkeypatch):
        import src.endpoint_resolver as er
        monkeypatch.setattr(er, "resolve_url", lambda url: url)
        monkeypatch.setattr(llm_core, "_fingerprint_is_lmstudio", lambda u: True)
        result = er.build_chat_url("http://192.168.1.5:1234/v1")
        assert result == "http://192.168.1.5:1234/v1/chat/completions"
