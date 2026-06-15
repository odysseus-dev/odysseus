"""Tests for the Claude (Anthropic) subscription token provider.

Imports the real helpers from ``src.claude_subscription`` / ``src.llm_core`` /
``src.endpoint_resolver`` so the provider wiring (detection, auth headers, URL
building) is actually exercised. Network calls are monkeypatched.
"""

import json

import pytest

from src import claude_subscription as cs
from src import llm_core
from src.endpoint_resolver import build_chat_url, build_models_url, build_headers

SENTINEL = cs.DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL  # https://api.anthropic.com/oauth


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


# ── Provider detection ──

class TestDetection:
    def test_is_claude_subscription_base_positive(self):
        assert cs.is_claude_subscription_base(SENTINEL)
        assert cs.is_claude_subscription_base("https://api.anthropic.com/oauth/")

    def test_is_claude_subscription_base_negative(self):
        assert not cs.is_claude_subscription_base("https://api.anthropic.com")
        assert not cs.is_claude_subscription_base("https://api.anthropic.com/v1")
        assert not cs.is_claude_subscription_base("https://api.openai.com/oauth")

    def test_detect_provider_subscription_vs_anthropic(self):
        assert llm_core._detect_provider(SENTINEL) == "claude-subscription"
        assert llm_core._detect_provider("https://api.anthropic.com") == "anthropic"
        assert llm_core._detect_provider("https://api.anthropic.com/v1") == "anthropic"

    def test_is_anthropic_like(self):
        assert llm_core._is_anthropic_like("anthropic")
        assert llm_core._is_anthropic_like("claude-subscription")
        assert not llm_core._is_anthropic_like("openai")


# ── URL building ──

class TestUrls:
    def test_normalize_strips_oauth_sentinel(self):
        assert llm_core._normalize_anthropic_url(SENTINEL) == "https://api.anthropic.com/v1/messages"
        assert llm_core._normalize_anthropic_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"

    def test_build_chat_url_keeps_subscription_routing(self):
        chat_url = build_chat_url(SENTINEL)
        assert llm_core._detect_provider(chat_url) == "claude-subscription"
        assert llm_core._normalize_anthropic_url(chat_url) == "https://api.anthropic.com/v1/messages"

    def test_build_models_url(self):
        assert build_models_url(SENTINEL) == "https://api.anthropic.com/v1/models"


# ── Auth headers ──

class TestHeaders:
    def test_oauth_headers_keep_bearer_and_add_beta(self):
        h = llm_core._build_anthropic_headers({"Authorization": "Bearer TOK"}, oauth=True)
        assert h["Authorization"] == "Bearer TOK"
        assert h["anthropic-beta"] == "oauth-2025-04-20"
        assert h["anthropic-version"] == "2023-06-01"
        assert "x-api-key" not in h

    def test_apikey_headers_convert_bearer(self):
        h = llm_core._build_anthropic_headers({"Authorization": "Bearer KEY"})
        assert h["x-api-key"] == "KEY"
        assert "Authorization" not in h
        assert "anthropic-beta" not in h

    def test_oauth_headers_do_not_duplicate_incoming_beta(self):
        h = llm_core._build_anthropic_headers(
            {"Authorization": "Bearer TOK", "anthropic-beta": "something-else"}, oauth=True
        )
        assert h["anthropic-beta"] == "oauth-2025-04-20"

    def test_build_headers_for_subscription(self):
        h = build_headers("ACCESS", SENTINEL)
        assert h["Authorization"] == "Bearer ACCESS"
        assert h["anthropic-beta"] == "oauth-2025-04-20"
        assert h["anthropic-version"] == "2023-06-01"
        assert "x-api-key" not in h

    def test_claude_oauth_headers_helper(self):
        h = cs.claude_oauth_headers("t")
        assert h["Authorization"] == "Bearer t"
        assert h["anthropic-beta"] == "oauth-2025-04-20"
        assert cs.claude_oauth_headers(None).get("Authorization") is None


# ── Payload (OAuth identity injection) ──

_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


class TestPayload:
    def test_oauth_prepends_identity(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.0, 16, oauth=True
        )
        assert p["system"][0]["text"] == _IDENTITY

    def test_oauth_guidance_block_after_identity(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.0, 16, oauth=True
        )
        guidance = p["system"][1]["text"].lower()
        # Empowering, not restrictive: keeps tool/agent powers, no refusals.
        assert "tools" in guidance and "never refuse" in guidance
        assert "not a terminal" not in guidance

    def test_oauth_keeps_user_system_after_identity_and_guidance(self):
        msgs = [{"role": "system", "content": "You are a pirate."}, {"role": "user", "content": "hi"}]
        p = llm_core._build_anthropic_payload("claude-opus-4-8", msgs, 0.0, 16, oauth=True)
        assert p["system"][0]["text"] == _IDENTITY            # identity first
        assert "Claude Code" in p["system"][1]["text"]        # guidance second
        assert p["system"][2]["text"] == "You are a pirate."  # user system last

    def test_non_oauth_has_no_identity(self):
        msgs = [{"role": "system", "content": "You are a pirate."}, {"role": "user", "content": "hi"}]
        p = llm_core._build_anthropic_payload("claude-opus-4-8", msgs, 0.0, 16, oauth=False)
        assert all(_IDENTITY not in b.get("text", "") for b in p.get("system", []))


# ── Effort gating + payload ──

class TestEffort:
    def test_supports_effort_matrix(self):
        sup = llm_core._anthropic_supports_effort
        assert sup("claude-opus-4-8")
        assert sup("claude-opus-4-5-20251101")
        assert sup("claude-sonnet-4-6")
        assert sup("claude-fable-5")
        assert not sup("claude-opus-4-1-20250805")     # 4.1 < 4.5
        assert not sup("claude-opus-4-20250514")       # 4.0 (dated) < 4.5
        assert not sup("claude-sonnet-4-5-20250929")   # sonnet 4.5 < 4.6
        assert not sup("claude-haiku-4-5-20251001")
        assert not sup("gpt-4o")

    def test_effort_payload_on_supported_model(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.7, 64, effort="high"
        )
        assert p["output_config"] == {"effort": "high"}
        assert p["thinking"] == {"type": "adaptive"}
        assert "temperature" not in p  # omitted on the effort path

    def test_effort_ignored_on_unsupported_model(self):
        p = llm_core._build_anthropic_payload(
            "claude-haiku-4-5-20251001", [{"role": "user", "content": "hi"}], 0.7, 64, effort="high"
        )
        assert "output_config" not in p
        assert p.get("temperature") == 0.7  # haiku still takes temperature

    def test_invalid_effort_ignored(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.7, 64, effort="turbo"
        )
        assert "output_config" not in p

    def test_no_effort_is_unchanged(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.7, 64
        )
        assert "output_config" not in p

    def test_oauth_defaults_to_high_effort(self):
        # Subscription (oauth) defaults to adaptive thinking + high effort.
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.7, 64, oauth=True
        )
        assert p["output_config"] == {"effort": "high"}
        assert p["thinking"] == {"type": "adaptive"}
        assert "temperature" not in p

    def test_oauth_default_effort_skipped_for_haiku(self):
        p = llm_core._build_anthropic_payload(
            "claude-haiku-4-5-20251001", [{"role": "user", "content": "hi"}], 0.7, 64, oauth=True
        )
        assert "output_config" not in p
        assert p.get("temperature") == 0.7

    def test_explicit_effort_overrides_oauth_default(self):
        p = llm_core._build_anthropic_payload(
            "claude-opus-4-8", [{"role": "user", "content": "hi"}], 0.7, 64, oauth=True, effort="low"
        )
        assert p["output_config"] == {"effort": "low"}


# ── 1M context window ──

class TestContext1M:
    def test_supports_1m_matrix(self):
        s = llm_core._anthropic_supports_1m_context
        assert s("claude-opus-4-8")
        assert s("claude-opus-4-6")
        assert s("claude-sonnet-4-6")
        assert s("claude-fable-5")
        assert not s("claude-opus-4-5-20251101")     # 4.5 < 4.6
        assert not s("claude-sonnet-4-5-20250929")
        assert not s("claude-haiku-4-5-20251001")
        assert not s("gpt-4o")

    def test_headers_add_context_1m_for_oauth(self):
        h = llm_core._build_anthropic_headers(
            {"Authorization": "Bearer T"}, oauth=True, model="claude-opus-4-8"
        )
        betas = h["anthropic-beta"].split(",")
        assert "oauth-2025-04-20" in betas
        assert "context-1m-2025-08-07" in betas
        assert h["Authorization"] == "Bearer T"

    def test_headers_context_1m_for_apikey(self):
        h = llm_core._build_anthropic_headers(
            {"Authorization": "Bearer K"}, oauth=False, model="claude-opus-4-8"
        )
        assert h["anthropic-beta"] == "context-1m-2025-08-07"
        assert h["x-api-key"] == "K"

    def test_headers_no_context_1m_for_haiku(self):
        h = llm_core._build_anthropic_headers(
            {"Authorization": "Bearer T"}, oauth=True, model="claude-haiku-4-5"
        )
        assert h["anthropic-beta"] == "oauth-2025-04-20"


# ── Pasted-credential parsing ──

class TestParse:
    def test_bare_token(self):
        access, refresh, expires = cs.parse_pasted_credentials("  sk-ant-oat01-abc  ")
        assert access == "sk-ant-oat01-abc"
        assert refresh == ""
        assert expires is None

    def test_keychain_json(self):
        blob = json.dumps({"claudeAiOauth": {
            "accessToken": "AAA", "refreshToken": "RRR", "expiresAt": 1781478321055,
        }})
        access, refresh, expires = cs.parse_pasted_credentials(blob)
        assert access == "AAA"
        assert refresh == "RRR"
        assert expires is not None and expires.year >= 2026

    def test_flat_snake_case_json(self):
        blob = json.dumps({"access_token": "X", "refresh_token": "Y"})
        access, refresh, expires = cs.parse_pasted_credentials(blob)
        assert (access, refresh, expires) == ("X", "Y", None)

    def test_empty(self):
        assert cs.parse_pasted_credentials("") == ("", "", None)

    def test_bad_json_raises(self):
        with pytest.raises(cs.ClaudeSubscriptionReauthRequired):
            cs.parse_pasted_credentials("{not valid json")


# ── Model discovery ──

class TestModelDiscovery:
    def test_fetch_available_models_filters_claude(self, monkeypatch):
        payload = {"data": [
            {"id": "claude-opus-4-8"},
            {"id": "claude-haiku-4-5-20251001"},
            {"id": "not-a-claude-model"},
            {"id": "claude-sonnet-4-6"},
        ]}

        def fake_get(url, headers=None, timeout=None):
            assert "/v1/models" in url
            assert headers.get("anthropic-beta") == "oauth-2025-04-20"
            return _FakeResp(200, payload)

        monkeypatch.setattr(cs.httpx, "get", fake_get)
        assert cs.fetch_available_models("ACCESS") == [
            "claude-opus-4-8", "claude-haiku-4-5-20251001", "claude-sonnet-4-6",
        ]

    def test_fetch_available_models_empty_on_error(self, monkeypatch):
        monkeypatch.setattr(cs.httpx, "get", lambda *a, **k: _FakeResp(401, {}))
        assert cs.fetch_available_models("ACCESS") == []
        assert cs.fetch_available_models("") == []


# ── Refresh (only used when a refresh token was provided) ──

class TestRefresh:
    def test_refresh_shape(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=None, follow_redirects=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp(200, {"access_token": "A2", "expires_in": 28800})

        monkeypatch.setattr(cs.httpx, "post", fake_post)
        out = cs.refresh_oauth_tokens("REFRESH")
        assert out["access_token"] == "A2"
        assert captured["url"] == cs.CLAUDE_OAUTH_TOKEN_URL
        assert captured["json"]["grant_type"] == "refresh_token"
        assert captured["json"]["refresh_token"] == "REFRESH"
        assert captured["json"]["client_id"] == cs.CLAUDE_OAUTH_CLIENT_ID

    def test_refresh_without_token_raises(self):
        with pytest.raises(cs.ClaudeSubscriptionReauthRequired):
            cs.refresh_oauth_tokens("")


# ── Expiry decision (pure) ──

class TestExpiry:
    def test_access_token_is_expiring(self):
        from datetime import datetime, timedelta

        def now():
            return datetime(2026, 1, 1, 12, 0, 0)

        assert cs._access_token_is_expiring(None, now, 300) is True
        assert cs._access_token_is_expiring(now() + timedelta(hours=2), now, 300) is False
        assert cs._access_token_is_expiring(now() + timedelta(seconds=60), now, 300) is True
        assert cs._access_token_is_expiring(now() - timedelta(seconds=1), now, 300) is True
