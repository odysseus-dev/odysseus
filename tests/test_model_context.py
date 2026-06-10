"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import sys
import types

import pytest

import src.model_context as model_context
from src.model_context import _is_local_endpoint, estimate_tokens, _lookup_known


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return ("eq", self.name, value)


class _ModelEndpoint:
    is_enabled = _Column("is_enabled")


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *conditions):
        for condition in conditions:
            if isinstance(condition, tuple) and condition[0] == "eq":
                _, field, value = condition
                self.rows = [row for row in self.rows if getattr(row, field) == value]
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def query(self, model):
        return _Query(self.rows)

    def close(self):
        pass


def _install_endpoint_db(monkeypatch, rows):
    mod = types.ModuleType("core.database")
    mod.ModelEndpoint = _ModelEndpoint
    mod.SessionLocal = lambda: _Db(rows)
    monkeypatch.setitem(sys.modules, "core.database", mod)


class TestIsLocalEndpoint:
    def test_localhost(self):
        assert _is_local_endpoint("http://localhost:5000/v1/chat/completions") is True

    def test_loopback_ipv4(self):
        assert _is_local_endpoint("http://127.0.0.1:8080/v1/chat/completions") is True

    def test_private_192_168(self):
        assert _is_local_endpoint("http://192.168.1.1:11434/v1/chat/completions") is True

    def test_private_10(self):
        assert _is_local_endpoint("http://10.0.0.5:8000/v1/chat/completions") is True

    def test_tailscale_100(self):
        # 100.64.0.0/10 is the CGNAT range Tailscale uses.
        assert _is_local_endpoint("http://100.64.0.1:5000/v1/chat/completions") is True

    def test_configured_tailscale_proxy_is_remote(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [
            types.SimpleNamespace(
                base_url="http://100.117.136.97:34521/v1",
                endpoint_kind="proxy",
                api_key="fake-key",
                is_enabled=True,
            )
        ])

        assert _is_local_endpoint("http://100.117.136.97:34521/v1/chat/completions") is False

    def test_openai_is_remote(self):
        assert _is_local_endpoint("https://api.openai.com/v1/chat/completions") is False

    def test_anthropic_is_remote(self):
        assert _is_local_endpoint("https://api.anthropic.com/v1/messages") is False

    def test_empty_url(self):
        assert _is_local_endpoint("") is False

    def test_malformed_url(self):
        assert _is_local_endpoint("not-a-url") is False


class TestEstimateTokens:
    def test_empty_list(self):
        assert estimate_tokens([]) == 0

    def test_single_short_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(5 * 0.3) = 4 + 1 = 5
        assert tokens == 5

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi there"},
        ]
        tokens = estimate_tokens(messages)
        assert tokens > 0
        # Each message adds 4 overhead + chars * 0.3
        assert tokens == 4 + int(16 * 0.3) + 4 + int(8 * 0.3)

    def test_multimodal_content_list(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        tokens = estimate_tokens(messages)
        # 4 overhead + int(19 * 0.3) for the text item; image_url is ignored
        assert tokens == 4 + int(19 * 0.3)

    def test_missing_content_key(self):
        messages = [{"role": "assistant"}]
        tokens = estimate_tokens(messages)
        # 4 overhead + 0 content
        assert tokens == 4

    def test_scales_with_length(self):
        short = estimate_tokens([{"role": "user", "content": "short"}])
        long_text = "a" * 10000
        long = estimate_tokens([{"role": "user", "content": long_text}])
        assert long > short * 10


class TestLookupKnown:
    def test_claude_sonnet(self):
        assert _lookup_known("claude-sonnet-4-5") == 200000

    def test_gpt4o(self):
        assert _lookup_known("gpt-4o") == 128000

    def test_deepseek_r1(self):
        assert _lookup_known("deepseek-r1") == 64000

    def test_gemini_pro(self):
        assert _lookup_known("gemini-2.5-pro") == 1048576

    def test_unknown_model(self):
        assert _lookup_known("totally-unknown-model-xyz") is None

    def test_namespaced_model(self):
        """Models prefixed with provider/ should still match."""
        result = _lookup_known("openrouter/deepseek-r1")
        assert result == 64000

    def test_model_with_tag(self):
        """Models with :free or :extended suffixes should still match."""
        result = _lookup_known("deepseek-r1:free")
        assert result == 64000

    def test_o1_mini_not_shadowed_by_o1(self):
        """'o1' (200k) precedes 'o1-mini' (128k) in the table; longest match wins."""
        assert _lookup_known("o1-mini") == 128000

    def test_o1_full(self):
        assert _lookup_known("o1") == 200000

    def test_gpt4o_mini_not_shadowed_by_gpt4(self):
        assert _lookup_known("gpt-4o-mini") == 128000

    def test_gpt4_base(self):
        assert _lookup_known("gpt-4") == 8192


class TestGetContextLength:
    def setup_method(self):
        model_context._context_cache.clear()

    def test_local_endpoint_requeries_same_model_after_restart(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return 8192 if len(calls) == 1 else 27000

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "http://127.0.0.1:8000/v1/chat/completions"
        model = "Qwen/Qwen3-14B"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 8192
        assert second == 27000
        assert len(calls) == 2

    def test_remote_endpoint_keeps_cached_context(self, monkeypatch):
        calls = []

        def fake_query(endpoint_url, model):
            calls.append((endpoint_url, model))
            return 200000 if len(calls) == 1 else 12345

        monkeypatch.setattr(model_context, "_query_context_length", fake_query)

        endpoint = "https://api.openai.com/v1/chat/completions"
        model = "gpt-5"

        first = model_context.get_context_length(endpoint, model)
        second = model_context.get_context_length(endpoint, model)

        assert first == 200000
        assert second == 200000
        assert len(calls) == 1

    def test_configured_proxy_uses_default_without_model_listing(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [
            types.SimpleNamespace(
                base_url="http://100.117.136.97:34521/v1",
                endpoint_kind="proxy",
                api_key="fake-key",
                is_enabled=True,
            )
        ])
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(args)
            raise AssertionError("/models should not be queried for configured proxy context")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

        endpoint = "http://100.117.136.97:34521/v1/chat/completions"
        first = model_context.get_context_length(endpoint, "unknown-proxy-model")
        second = model_context.get_context_length(endpoint, "unknown-proxy-model")

        assert first == model_context.DEFAULT_CONTEXT
        assert second == model_context.DEFAULT_CONTEXT
        assert calls == []


class TestConfiguredEndpointKindPrivateHost:
    """A self-hosted server on a loopback / private / Tailscale host must NOT be
    auto-classified as a remote proxy just because it has an api_key and serves
    on /v1 — that would skip the local context-window probe in
    _query_context_length and fall back to the static known-model guess."""

    def _kind(self, monkeypatch, base_url, *, api_key="fake-key", kind="auto"):
        _install_endpoint_db(monkeypatch, [
            types.SimpleNamespace(
                base_url=base_url,
                endpoint_kind=kind,
                api_key=api_key,
                is_enabled=True,
            )
        ])
        return model_context._configured_endpoint_kind(base_url + "/chat/completions")

    def test_tailscale_host_with_key_is_not_proxy(self, monkeypatch):
        # Representative of the _PRIVATE_PREFIXES branch (100./192.168./10./172.x);
        # all share the same host.startswith(_PRIVATE_PREFIXES) check.
        assert self._kind(monkeypatch, "http://100.117.136.97:34521/v1") == "auto"

    def test_loopback_host_with_key_is_not_proxy(self, monkeypatch):
        assert self._kind(monkeypatch, "http://127.0.0.1:1234/v1") == "auto"

    def test_public_host_with_key_is_proxy(self, monkeypatch):
        # Regression guard: a genuine remote proxy (public host + api_key + /v1)
        # must still be detected as a proxy so we skip the /models catalog.
        assert self._kind(monkeypatch, "https://llm.example.com/v1") == "proxy"

    def test_explicit_proxy_kind_short_circuits(self, monkeypatch):
        # An explicitly configured kind wins regardless of host.
        assert self._kind(
            monkeypatch, "http://100.117.136.97:34521/v1", kind="proxy") == "proxy"

    def test_public_ollama_carveout_is_not_proxy(self, monkeypatch):
        # The proxy branch also carves out Ollama, on the same `if` the
        # private-host fix touches: a public host is NOT a proxy when it serves
        # on Ollama's default port 11434, nor when "ollama" is in the host.
        assert self._kind(monkeypatch, "http://203.0.113.10:11434/v1") == "auto"
        assert self._kind(monkeypatch, "https://ollama.example.com/v1") == "auto"


class TestQueryContextLengthLMStudio:
    """_query_context_length should read LM Studio's loaded_context_length from
    its native /api/v0/models when the llama.cpp /slots probe is unavailable."""

    def setup_method(self):
        model_context._context_cache.clear()

    def _patch_httpx(self, monkeypatch, models_payload, *, slots_ok=False):
        class _Resp:
            def __init__(self, ok, payload):
                self.is_success = ok
                self._payload = payload

            def json(self):
                return self._payload

        def fake_get(url, **kwargs):
            if url.endswith("/slots"):
                if slots_ok:
                    return _Resp(True, [{"n_ctx": 999}])
                raise ConnectionError("no llama.cpp here")
            if url.endswith("/api/v0/models"):
                return _Resp(True, models_payload)
            # Generic OpenAI-compatible /v1/models — reports nothing useful here.
            return _Resp(False, {})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)

    def test_reads_loaded_context_length(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [])  # no configured endpoint -> local
        self._patch_httpx(monkeypatch, {
            "data": [
                {"id": "qwen/qwen3-14b", "loaded_context_length": 32768,
                 "max_context_length": 131072},
            ],
        })
        ctx = model_context._query_context_length(
            "http://127.0.0.1:1234/v1/chat/completions", "qwen3-14b")
        assert ctx == 32768

    def test_namespaced_model_id_matches(self, monkeypatch):
        _install_endpoint_db(monkeypatch, [])
        self._patch_httpx(monkeypatch, {
            "data": [{"id": "lmstudio-community/Qwen3-14B-GGUF",
                      "loaded_context_length": 24000}],
        })
        ctx = model_context._query_context_length(
            "http://127.0.0.1:1234/v1/chat/completions", "Qwen3-14B-GGUF")
        assert ctx == 24000

    def test_missing_loaded_context_falls_back_to_known_table(self, monkeypatch):
        # The bug being fixed: without loaded_context_length we fall back to the
        # static known-model guess (qwen3 -> 131072), the value that overpacks.
        # This case alone reaches the generic /models fallback, which imports
        # build_models_url (-> the real core.database); drop any stub core.database
        # so _configured_endpoint_kind hits its "not imported" guard and the real
        # module is used downstream.
        monkeypatch.delitem(sys.modules, "core.database", raising=False)
        self._patch_httpx(monkeypatch, {
            "data": [{"id": "qwen3-14b"}],  # no loaded_context_length reported
        })
        ctx = model_context._query_context_length(
            "http://127.0.0.1:1234/v1/chat/completions", "qwen3")
        assert ctx == 131072

    def test_slots_takes_precedence_over_lmstudio(self, monkeypatch):
        # llama.cpp /slots reports the true serving context; prefer it.
        _install_endpoint_db(monkeypatch, [])
        self._patch_httpx(monkeypatch, {
            "data": [{"id": "qwen3-14b", "loaded_context_length": 32768}],
        }, slots_ok=True)
        ctx = model_context._query_context_length(
            "http://127.0.0.1:1234/v1/chat/completions", "qwen3-14b")
        assert ctx == 999
