"""Provider classification from a base URL (REAL src.llm_core).

ROADMAP "Backend → more tests around ... provider setup" and "Provider
setup/probing audit for Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, and
DeepSeek". `test_provider_endpoints.py` already pins URL/header *building*; this
module pins the two pieces of provider setup that decide WHICH provider an
endpoint is:

  * `_detect_provider`  — host-based provider identification (drives payload
    shape, auth headers, and the /v1 collapse). The look-alike-host and
    domain-in-path cases guard the hostname (not substring) matching.
  * `_provider_label`   — the human name shown in degraded-state messages.

Upstream-error formatting lives in `test_provider_classification_errors.py` and
the token-param quirk in `test_provider_classification_token_params.py`.

conftest.py stubs the heavy deps (sqlalchemy, src.database), so importing the
real module is side-effect free.
"""
import pytest

from src.llm_core import (
    _detect_provider,
    _provider_label,
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
        ("https://integrate.api.nvidia.com/v1", "nvidia"),
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
        ("https://integrate.api.nvidia.com/v1", "NVIDIA"),
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
