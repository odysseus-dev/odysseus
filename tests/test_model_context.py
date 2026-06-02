"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import httpx
import pytest

import src.model_context as model_context
from src.model_context import DEFAULT_CONTEXT, _is_local_endpoint, estimate_tokens, _lookup_known


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

    def test_host_docker_internal(self):
        assert _is_local_endpoint("http://host.docker.internal:11434/v1/chat/completions") is True

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


class TestQueryContextLength:
    OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
    REMOTE_URL = "https://api.openai.com/v1/chat/completions"

    def _response(self, method, url, status, body):
        req = httpx.Request(method, url)
        return httpx.Response(status, request=req, json=body)

    def test_ollama_api_show_used_for_local_endpoint(self, monkeypatch):
        post_calls = []
        get_calls = []

        def fake_get(url, timeout=None):
            get_calls.append(url)
            if url.endswith("/slots"):
                raise httpx.ConnectError("slots unavailable")
            return self._response("GET", url, 200, {"data": []})

        def fake_post(url, json=None, timeout=None):
            post_calls.append((url, json))
            return self._response(
                "POST",
                url,
                200,
                {"model_info": {"qwen3.context_length": 40960}},
            )

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(self.OLLAMA_URL, "qwen3:14b")

        assert result == 40960
        assert post_calls == [("http://localhost:11434/api/show", {"name": "qwen3:14b"})]
        assert not any(url.endswith("/models") for url in get_calls)

    def test_api_show_404_falls_through_to_models(self, monkeypatch):
        def fake_get(url, timeout=None):
            if url.endswith("/slots"):
                raise httpx.ConnectError("slots unavailable")
            return self._response(
                "GET",
                url,
                200,
                {"data": [{"id": "llama3:8b", "context_length": 8192}]},
            )

        def fake_post(url, json=None, timeout=None):
            return self._response("POST", url, 404, {})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(
            "http://localhost:11434/v1/chat/completions",
            "llama3:8b",
        )

        assert result == 8192

    def test_api_show_not_tried_for_non_ollama_local_endpoint(self, monkeypatch):
        post_calls = []

        def fake_get(url, timeout=None):
            if url.endswith("/slots"):
                raise httpx.ConnectError("slots unavailable")
            return self._response(
                "GET",
                url,
                200,
                {"data": [{"id": "local-model", "context_length": 16384}]},
            )

        def fake_post(url, json=None, timeout=None):
            post_calls.append(url)
            return self._response("POST", url, 200, {})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(
            "http://localhost:8080/v1/chat/completions",
            "local-model",
        )

        assert result == 16384
        assert post_calls == []

    def test_api_show_connection_error_falls_through_to_known_model(self, monkeypatch):
        def fake_get(url, timeout=None):
            raise httpx.ConnectError("offline")

        def fake_post(url, json=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(self.OLLAMA_URL, "qwen3:14b")

        assert result == 131072

    def test_api_show_connection_error_falls_through_to_default_context(self, monkeypatch):
        def fake_get(url, timeout=None):
            raise httpx.ConnectError("offline")

        def fake_post(url, json=None, timeout=None):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(self.OLLAMA_URL, "unknown-local-model")

        assert result == DEFAULT_CONTEXT

    def test_api_show_not_tried_for_remote_endpoint(self, monkeypatch):
        post_calls = []

        def fake_get(url, timeout=None):
            return self._response(
                "GET",
                url,
                200,
                {"data": [{"id": "gpt-4o", "context_length": 128000}]},
            )

        def fake_post(url, json=None, timeout=None):
            post_calls.append(url)
            return self._response("POST", url, 200, {})

        monkeypatch.setattr(model_context.httpx, "get", fake_get)
        monkeypatch.setattr(model_context.httpx, "post", fake_post)

        result = model_context._query_context_length(self.REMOTE_URL, "gpt-4o")

        assert result == 128000
        assert post_calls == []
