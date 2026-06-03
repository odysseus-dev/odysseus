"""Provider classification and upstream-error formatting (REAL src.llm_core).

ROADMAP "Backend → more tests around ... provider setup" and "Provider
setup/probing audit for Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, and
DeepSeek". `test_provider_endpoints.py` already pins URL/header *building*; this
module pins the two pieces of provider setup that decide WHICH provider an
endpoint is and how its failures are reported to the user:

  * `_detect_provider`  — host-based provider identification (drives payload
    shape, auth headers, and the /v1 collapse). The look-alike-host and
    domain-in-path cases guard the hostname (not substring) matching.
  * `_provider_label`   — the human name shown in degraded-state messages.
  * `_format_upstream_error` — turns a raw upstream HTTP status + body into the
    one-line, provider-aware message the UI shows ("Provider probes" degraded
    reporting in the roadmap).
  * `_uses_max_completion_tokens` — the gpt-5 / o-series quirk that the probe
    and chat payload builders branch on.

conftest.py stubs the heavy deps (sqlalchemy, src.database), so importing the
real module is side-effect free.
"""
import pytest

from src.llm_core import (
    _detect_provider,
    _provider_label,
    _format_upstream_error,
    _uses_max_completion_tokens,
)


# ── _detect_provider ──
# Matches on hostname (exact or subdomain), never substring, and falls back to
# the OpenAI-compatible default for everything it doesn't special-case.

class TestDetectProvider:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.anthropic.com", "anthropic"),
        ("https://api.anthropic.com/v1", "anthropic"),
        ("https://anthropic.com/v1", "anthropic"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.groq.com/openai/v1", "groq"),
        ("http://localhost:11434/api", "ollama"),
        ("https://ollama.com", "ollama"),
        # xAI, DeepSeek and Gemini's OpenAI-compatible surface are NOT
        # special-cased — they speak the OpenAI dialect, so the generic
        # "openai" path is correct, not a missed provider.
        ("https://api.openai.com/v1", "openai"),
        ("https://api.x.ai/v1", "openai"),
        ("https://api.deepseek.com", "openai"),
        ("https://generativelanguage.googleapis.com/v1beta/openai", "openai"),
        # Ollama's OpenAI-compatible /v1 surface is generic, not native ollama.
        ("http://localhost:11434/v1", "openai"),
    ])
    def test_known_providers(self, url, expected):
        assert _detect_provider(url) == expected

    def test_lookalike_host_is_not_matched(self):
        # Host merely *starts* with the provider domain as a label — a classic
        # substring-match trap (anthropic.com.evil.example is not Anthropic).
        assert _detect_provider("https://anthropic.com.evil.example/v1") == "openai"

    def test_provider_domain_in_path_is_not_matched(self):
        # The provider domain appears only in the path, not the host.
        assert _detect_provider("https://proxy.example.com/anthropic.com/v1") == "openai"

    def test_trailing_dot_host_still_matches(self):
        # A fully-qualified host with a trailing dot is still that host.
        assert _detect_provider("https://api.anthropic.com./v1") == "anthropic"

    @pytest.mark.parametrize("url", ["", None, "not a url", "://broken"])
    def test_unidentifiable_falls_back_to_openai(self, url):
        assert _detect_provider(url) == "openai"


# ── _provider_label ──
# Human-friendly name used in error/degraded-state messages.

class TestProviderLabel:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.anthropic.com/v1", "Anthropic"),
        ("https://ollama.com", "Ollama Cloud"),
        ("https://api.x.ai/v1", "xAI"),
        ("https://api.openai.com/v1", "OpenAI"),
        ("https://openrouter.ai/api/v1", "OpenRouter"),
        ("https://api.groq.com/openai/v1", "Groq"),
        ("https://api.mistral.ai/v1", "Mistral"),
        ("https://api.deepseek.com", "DeepSeek"),
        ("https://generativelanguage.googleapis.com/v1beta/openai", "Google"),
        ("https://api.together.xyz/v1", "Together"),
        ("https://api.together.ai/v1", "Together"),
        ("https://api.fireworks.ai/inference/v1", "Fireworks"),
        ("http://localhost:11434/api", "Ollama"),
    ])
    def test_known_labels(self, url, expected):
        assert _provider_label(url) == expected

    def test_local_non_ollama_endpoint(self):
        # A loopback host that isn't on the native Ollama /api path is just a
        # generic local endpoint (e.g. an OpenAI-compatible local server).
        assert _provider_label("http://localhost:8080/v1") == "local endpoint"

    def test_unknown_host_returns_host(self):
        assert _provider_label("https://api.unknown-llm.example/v1") == "api.unknown-llm.example"

    @pytest.mark.parametrize("url", ["", None])
    def test_empty_returns_generic(self, url):
        assert _provider_label(url) == "provider"


# ── _format_upstream_error ──
# Status + body → one-line provider-aware sentence.

class TestFormatUpstreamError:
    def test_401_rejects_key_with_provider_and_detail(self):
        msg = _format_upstream_error(
            401, '{"error": {"message": "Invalid API key"}}', "https://api.x.ai/v1"
        )
        assert msg.startswith("xAI rejected the API key")
        assert "Invalid API key" in msg
        assert "re-paste the key" in msg

    def test_403_denies_access(self):
        msg = _format_upstream_error(
            403, '{"error": {"message": "Forbidden"}}', "https://api.openai.com/v1"
        )
        assert "OpenAI denied access (403)" in msg
        assert "Forbidden" in msg

    def test_404_points_at_base_url(self):
        msg = _format_upstream_error(404, "", "https://api.groq.com/openai/v1")
        assert msg == "Groq returned 404 — check the base URL and model name."

    def test_429_rate_limited(self):
        msg = _format_upstream_error(
            429, '{"error": {"message": "slow down"}}', "https://api.anthropic.com"
        )
        assert msg.startswith("Anthropic rate-limited the request (429).")
        assert "slow down" in msg

    def test_5xx_reported_as_outage(self):
        msg = _format_upstream_error(503, "", "https://api.deepseek.com")
        assert msg == "DeepSeek is having an outage (HTTP 503)."

    def test_other_status_passthrough(self):
        msg = _format_upstream_error(418, "", "https://api.openai.com/v1")
        assert msg == "OpenAI returned HTTP 418"

    def test_string_error_field(self):
        msg = _format_upstream_error(401, '{"error": "bad key"}', "https://api.openai.com/v1")
        assert "bad key" in msg

    def test_plain_text_body_used_as_detail(self):
        msg = _format_upstream_error(500, "upstream exploded", "https://api.openai.com/v1")
        assert "OpenAI is having an outage (HTTP 500)." in msg
        assert "upstream exploded" in msg

    def test_bytes_body_is_decoded(self):
        msg = _format_upstream_error(
            401, b'{"error": {"message": "nope"}}', "https://api.openai.com/v1"
        )
        assert "nope" in msg

    def test_unknown_url_falls_back_to_generic_label(self):
        msg = _format_upstream_error(401, "", "")
        assert msg.startswith("provider rejected the API key")


# ── _uses_max_completion_tokens ──
# gpt-5 / o-series need `max_completion_tokens`; everything else `max_tokens`.

class TestUsesMaxCompletionTokens:
    @pytest.mark.parametrize("model", [
        "gpt-5", "gpt-5.2", "gpt-5-mini", "o1", "o1-preview", "o3", "o3-mini",
        "o4-mini", "gpt-4.5", "gpt-4.5-preview", "openrouter/openai/o3",
    ])
    def test_requires_max_completion_tokens(self, model):
        assert _uses_max_completion_tokens(model) is True

    @pytest.mark.parametrize("model", [
        # gpt-4o must NOT be confused with the o-series ("o4"/"o1" tokens).
        "gpt-4o", "gpt-4o-mini", "gpt-4.1", "claude-opus-4", "llama-3.3-70b",
        "deepseek-chat", "", None,
    ])
    def test_uses_plain_max_tokens(self, model):
        assert _uses_max_completion_tokens(model) is False
