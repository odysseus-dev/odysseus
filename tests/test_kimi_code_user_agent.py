"""Kimi Code User-Agent fallback list and 403 detection."""
from src.llm_core import (
    KIMI_CODE_USER_AGENTS,
    KIMI_CODE_USER_AGENT,
    _is_kimi_code_access_denied,
    _is_kimi_code_url,
    _kimi_code_base_key,
    _kimi_code_ua_cache,
    _kimi_code_ua_candidates,
    _remember_kimi_code_user_agent,
    httpx_post_kimi_aware,
)


class TestKimiCodeUserAgents:
    def test_default_is_first_fallback(self):
        assert KIMI_CODE_USER_AGENT == KIMI_CODE_USER_AGENTS[0]

    def test_multiple_fallbacks_configured(self):
        assert len(KIMI_CODE_USER_AGENTS) >= 3
        assert "KimiCLI/1.0" in KIMI_CODE_USER_AGENTS

    def test_detects_coding_agent_403(self):
        body = '{"error":{"message":"only available for Coding Agents","type":"access_terminated_error"}}'
        assert _is_kimi_code_access_denied(403, body) is True

    def test_non_403_not_access_denied(self):
        assert _is_kimi_code_access_denied(401, "unauthorized") is False

    def test_ua_candidates_prefers_cache(self):
        _kimi_code_ua_cache.clear()
        url = "https://api.kimi.com/coding/v1/chat/completions"
        _remember_kimi_code_user_agent(url, "Kilo-Code/1.0")
        candidates = _kimi_code_ua_candidates(url)
        assert candidates[0] == "Kilo-Code/1.0"
        assert len(candidates) == len(KIMI_CODE_USER_AGENTS)
        _kimi_code_ua_cache.clear()

    def test_non_kimi_url_has_no_candidates(self):
        assert _kimi_code_ua_candidates("https://api.openai.com/v1") == []

    def test_base_key_normalizes_chat_url(self):
        assert _kimi_code_base_key("https://api.kimi.com/coding/v1/chat/completions") == (
            "https://api.kimi.com/coding/v1"
        )

    def test_post_retries_next_user_agent_on_403(self, monkeypatch):
        _kimi_code_ua_cache.clear()
        calls = []

        class _Resp:
            def __init__(self, status, text=""):
                self.status_code = status
                self.content = text.encode()
                self.text = text

        def fake_post(url, headers=None, **kwargs):
            calls.append(headers.get("User-Agent"))
            if headers.get("User-Agent") == KIMI_CODE_USER_AGENTS[0]:
                return _Resp(403, '{"error":{"type":"access_terminated_error"}}')
            return _Resp(200, "{}")

        monkeypatch.setattr("src.llm_core.httpx.post", fake_post)
        url = "https://api.kimi.com/coding/v1/chat/completions"
        r = httpx_post_kimi_aware(url, {"Authorization": "Bearer x"}, json={})
        assert r.status_code == 200
        assert calls[0] == KIMI_CODE_USER_AGENTS[0]
        assert calls[1] == KIMI_CODE_USER_AGENTS[1]
        _kimi_code_ua_cache.clear()
