"""Tests for the Minimax provider integration.

Minimax (https://api.minimax.io/anthropic) exposes an Anthropic-API-compatible
Messages endpoint, so most of its dispatch reuses the existing Anthropic
primitives in `src/llm_core.py` and `src/endpoint_resolver.py`. These tests
guard the seams where Minimax diverges:

- Distinct provider string ("minimax") returned by `_detect_provider`
- Friendly "Minimax" label in error messages
- Reuse of `_build_anthropic_payload`/`_build_anthropic_headers`/SSE parser
- Temperature NOT clamped to [0, 1] for Minimax (Minimax accepts [0, 2])
- Dedicated `MINIMAX_MODELS` fallback list
- Anthropic URL/header helpers accept a Minimax base URL
- Static host sets (`_API_HOSTS`, `_SOTA_HOSTS`) include `api.minimax.io`
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from src.llm_core import (
    MINIMAX_MODELS,
    _build_anthropic_payload,
    _build_anthropic_headers,
    _detect_provider,
    _provider_label,
)
from src.endpoint_resolver import (
    _anthropic_api_root,
    build_chat_url,
    build_headers,
    build_models_url,
)


# ── Provider detection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://api.minimax.io/anthropic",
    "https://api.minimax.io/anthropic/v1",
    "https://api.minimax.io/anthropic/v1/messages",
    "https://minimax.io/anthropic",
    "https://minimax.io",
])
def test_detect_provider_minimax(url):
    assert _detect_provider(url) == "minimax"


@pytest.mark.parametrize("url,expected", [
    # Lookalike hosts must not be misclassified
    ("https://minimax.io.evil.test", "openai"),
    ("https://notminimax.io", "openai"),
])
def test_detect_provider_minimax_lookalike(url, expected):
    assert _detect_provider(url) == expected


# ── Provider label (used in error messages) ────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://api.minimax.io/anthropic",
    "https://minimax.io",
])
def test_provider_label_minimax(url):
    assert _provider_label(url) == "Minimax"


# ── Endpoint resolution: URL builders ──────────────────────────────────────

@pytest.mark.parametrize("base,expected", [
    # No trailing /v1 → chat URL appends /v1/messages after the /anthropic path
    ("https://api.minimax.io/anthropic",
     "https://api.minimax.io/anthropic/v1/messages"),
    # User supplies trailing /v1 → _anthropic_api_root strips it, then chat
    # builder re-appends /v1/messages. End result is still /anthropic/v1/messages.
    ("https://api.minimax.io/anthropic/v1",
     "https://api.minimax.io/anthropic/v1/messages"),
])
def test_build_chat_url_minimax(base, expected):
    assert build_chat_url(base) == expected


@pytest.mark.parametrize("base,expected", [
    ("https://api.minimax.io/anthropic",
     "https://api.minimax.io/anthropic/v1/models"),
    ("https://api.minimax.io/anthropic/v1",
     "https://api.minimax.io/anthropic/v1/models"),
])
def test_build_models_url_minimax(base, expected):
    assert build_models_url(base) == expected


def test_anthropic_api_root_strips_trailing_v1_for_minimax():
    # Trailing /v1 must collapse to the API root so the chat-URL builder can
    # re-append /v1/messages cleanly.
    assert _anthropic_api_root("https://api.minimax.io/anthropic/v1") == \
        "https://api.minimax.io/anthropic"


# ── Endpoint resolution: auth headers ──────────────────────────────────────

def test_build_headers_minimax_uses_x_api_key():
    headers = build_headers("test-key", "https://api.minimax.io/anthropic")
    assert headers.get("x-api-key") == "test-key"
    assert headers.get("anthropic-version") == "2023-06-01"
    # No bearer leakage.
    assert "Authorization" not in headers


def test_build_headers_minimax_no_api_key():
    headers = build_headers(None, "https://api.minimax.io/anthropic")
    assert "x-api-key" not in headers
    # anthropic-version is still set by build_headers regardless of key.
    assert headers.get("anthropic-version") == "2023-06-01"


# ── Anthropic payload builder: provider-aware temperature clamp ────────────

def _messages(text="hi"):
    return [{"role": "user", "content": text}]


def test_build_anthropic_payload_clamps_for_anthropic():
    # 1.2 must be clamped to 1.0 for native Anthropic (legacy behavior).
    payload = _build_anthropic_payload(
        "claude-sonnet-4", _messages(), temperature=1.2, max_tokens=64,
        provider="anthropic",
    )
    assert payload["temperature"] == 1.0


def test_build_anthropic_payload_skips_clamp_for_minimax():
    # Minimax documents [0, 2] so 1.2 must pass through unchanged.
    payload = _build_anthropic_payload(
        "MiniMax-M3", _messages(), temperature=1.2, max_tokens=64,
        provider="minimax",
    )
    assert payload["temperature"] == 1.2


def test_build_anthropic_payload_default_provider_keeps_legacy_behavior():
    # No provider argument → original Anthropic clamp applies (backwards compat).
    payload = _build_anthropic_payload(
        "claude-sonnet-4", _messages(), temperature=1.5, max_tokens=64,
    )
    assert payload["temperature"] == 1.0


def test_build_anthropic_headers_minimax_uses_x_api_key():
    # Even when the caller passes a Bearer-style Authorization, the helper must
    # rewrite it to x-api-key (this is how the Minimax auth surface works).
    h = _build_anthropic_headers({"Authorization": "Bearer test-key"})
    assert h.get("x-api-key") == "test-key"
    assert h.get("anthropic-version") == "2023-06-01"


# ── Hardcoded model list ───────────────────────────────────────────────────

def test_minimax_models_non_empty():
    assert isinstance(MINIMAX_MODELS, list)
    assert len(MINIMAX_MODELS) >= 4
    # The current flagship must be listed.
    assert "MiniMax-M3" in MINIMAX_MODELS


def test_minimax_models_distinct_from_anthropic():
    from src.llm_core import ANTHROPIC_MODELS
    assert set(MINIMAX_MODELS).isdisjoint(set(ANTHROPIC_MODELS))


# ── Static host sets (agent loop + teacher escalation) ─────────────────────

def test_api_hosts_includes_minimax():
    from src.agent_loop import _API_HOSTS
    assert "api.minimax.io" in _API_HOSTS


def test_sota_hosts_includes_minimax():
    from src.teacher_escalation import _SOTA_HOSTS
    assert "api.minimax.io" in _SOTA_HOSTS


# ── Provider parity (Anthropic and Minimax share primitives) ───────────────

def test_minimax_anthropic_same_url_building():
    # Building chat URLs for the two providers should produce equivalent
    # /v1/messages paths when their respective base URLs follow the same
    # /anthropic-style convention.
    anth = build_chat_url("https://api.anthropic.com")
    mini = build_chat_url("https://api.minimax.io/anthropic")
    assert anth.endswith("/v1/messages")
    assert mini.endswith("/v1/messages")
