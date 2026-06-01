"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import pytest
import httpx

import src.model_context as model_context
from src.model_context import _is_local_endpoint, estimate_tokens, _lookup_known, DEFAULT_CONTEXT


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


class TestQueryContextLength:
    """Tests for _query_context_length() HTTP probing logic."""

    OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
    REMOTE_URL = "https://api.openai.com/v1/chat/completions"

    def _make_response(self, status, json_body, url="http://localhost"):
        req = httpx.Request("GET", url)
        return httpx.Response(status, request=req, json=json_body)

    def _make_post_response(self, status, json_body, url="http://localhost"):
        req = httpx.Request("POST", url)
        return httpx.Response(status, request=req, json=json_body)

    def test_ollama_api_show_used_for_local(self, monkeypatch):
        """/api/show context_length is trusted over known value for local endpoints."""
        post_calls = []

        def fake_post(url, json=None, timeout=None):
            post_calls.append(url)
            return self._make_post_response(200, {
                "model_info": {"qwen3.context_length": 40960}
            }, url)

        def fake_get(url, timeout=None):
            # /slots not available, /v1/models returns no context_length
            req = httpx.Request("GET", url)
            if url.endswith("/slots"):
                raise httpx.ConnectError("no slots")
            return httpx.Response(200, request=req, json={"data": [{"id": "qwen3:14b"}]})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        result = model_context._query_context_length(self.OLLAMA_URL, "qwen3:14b")

        assert result == 40960
        assert any("/api/show" in url for url in post_calls)

    def test_api_show_404_falls_through_to_models(self, monkeypatch):
        """/api/show 404 (non-Ollama server) falls through to /v1/models."""
        def fake_post(url, json=None, timeout=None):
            req = httpx.Request("POST", url)
            return httpx.Response(404, request=req, json={})

        def fake_get(url, timeout=None):
            req = httpx.Request("GET", url)
            if url.endswith("/slots"):
                raise httpx.ConnectError("no slots")
            return httpx.Response(200, request=req, json={
                "data": [{"id": "llama3:8b", "context_length": 8192}]
            })

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        result = model_context._query_context_length(
            "http://localhost:8080/v1/chat/completions", "llama3:8b"
        )
        assert result == 8192

    def test_api_show_connection_error_falls_through(self, monkeypatch):
        """/api/show connection error is silently ignored."""
        def fake_post(url, json=None, timeout=None):
            raise httpx.ConnectError("refused")

        def fake_get(url, timeout=None):
            req = httpx.Request("GET", url)
            if url.endswith("/slots"):
                raise httpx.ConnectError("no slots")
            return httpx.Response(200, request=req, json={"data": []})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        # qwen3:14b known = 131072, api fails → returns known
        result = model_context._query_context_length(self.OLLAMA_URL, "qwen3:14b")
        assert result == 131072

    def test_api_show_not_tried_for_remote(self, monkeypatch):
        """/api/show is never queried for remote (cloud) endpoints."""
        post_calls = []

        def fake_post(url, json=None, timeout=None):
            post_calls.append(url)
            req = httpx.Request("POST", url)
            return httpx.Response(200, request=req, json={})

        def fake_get(url, timeout=None):
            req = httpx.Request("GET", url)
            return httpx.Response(200, request=req, json={
                "data": [{"id": "gpt-4o", "context_length": 128000}]
            })

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        model_context._query_context_length(self.REMOTE_URL, "gpt-4o")
        assert not any("/api/show" in url for url in post_calls)

    def test_all_http_fails_known_model_returns_known(self, monkeypatch):
        """All HTTP probes fail → returns known context window."""
        def fake_get(url, timeout=None):
            raise httpx.ConnectError("offline")

        def fake_post(url, json=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        result = model_context._query_context_length(self.OLLAMA_URL, "qwen3:14b")
        assert result == 131072

    def test_all_http_fails_unknown_model_returns_default(self, monkeypatch):
        """All HTTP probes fail + unknown model → DEFAULT_CONTEXT."""
        def fake_get(url, timeout=None):
            raise httpx.ConnectError("offline")

        def fake_post(url, json=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)
        model_context._context_cache.clear()

        result = model_context._query_context_length(self.OLLAMA_URL, "totally-unknown-xyz")
        assert result == DEFAULT_CONTEXT
