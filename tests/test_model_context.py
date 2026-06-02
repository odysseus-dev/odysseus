"""Tests for model_context.py — local endpoint detection, token estimation, known model lookup."""

import pytest

import src.model_context as model_context
from src.model_context import _is_local_endpoint, estimate_tokens, _lookup_known


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


class TestLookupKnownHyphenInsensitive:
    """Ollama reports model names without the hyphens the table keys use
    (`llama3.2` vs `llama-3.2`, `phi4` vs `phi-4`). `_lookup_known` must match
    both; otherwise these models fall back to DEFAULT_CONTEXT and over-allocate
    the KV cache once `num_ctx` is sent to Ollama.
    """

    @pytest.mark.parametrize("ollama_name, hyphenated_key", [
        ("phi4", "phi-4"),
        ("gemma3:4b", "gemma-3"),
        ("gemma2:9b", "gemma-2"),
        ("llama3.2:latest", "llama-3.2"),
        ("llama3.1:8b", "llama-3.1"),
        ("llama3.3", "llama-3.3"),
    ])
    def test_unhyphenated_ollama_name_matches_hyphenated_key(self, ollama_name, hyphenated_key):
        ctx = _lookup_known(ollama_name)
        assert ctx is not None, f"{ollama_name!r} should resolve, not fall back to DEFAULT"
        # Resolves to the same window as its hyphenated table key.
        assert ctx == _lookup_known(hyphenated_key)

    def test_phi4_resolves_to_real_window_not_default(self):
        # The headline regression: `phi4` used to miss and fall back to
        # DEFAULT_CONTEXT (128000) — an 8x over-allocation for a 16k model.
        assert _lookup_known("phi4") == 16000
        assert _lookup_known("phi4") != model_context.DEFAULT_CONTEXT

    def test_specific_known_windows(self):
        assert _lookup_known("gemma3:4b") == 128000
        assert _lookup_known("llama3.2:latest") == 131072

    def test_already_unhyphenated_keys_unchanged(self):
        # qwen keys were already unhyphenated and must keep working.
        assert _lookup_known("qwen3:8b") == 131072
        assert _lookup_known("qwen2.5:7b") == 131072
        # deepseek-r1 carries the hyphen in both the table and the Ollama name.
        assert _lookup_known("deepseek-r1:7b") == 64000

    def test_longest_match_invariant_preserved(self):
        # o1-mini must still win over o1 after de-hyphenation (not report 200k).
        assert _lookup_known("o1-mini") == _lookup_known("o1mini")
        assert _lookup_known("o1-mini") != _lookup_known("o1")
        # gpt-4o-mini must not be shadowed by gpt-4o / gpt-4.
        assert _lookup_known("gpt-4o-mini") == _lookup_known("gpt4omini")

    def test_unknown_model_returns_none(self):
        assert _lookup_known("totally-unknown-model-xyz") is None
