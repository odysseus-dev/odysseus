"""Kimi Code User-Agent fallback list and 403 detection."""
import pytest

from src import llm_core
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


KIMI_CHAT_URL = "https://api.kimi.com/coding/v1/chat/completions"


class _Resp:
    def __init__(self, status, text="{}"):
        self.status_code = status
        self.content = text.encode()
        self.text = text


class _FakeStreamResp(_Resp):
    async def aiter_lines(self):
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


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
        _remember_kimi_code_user_agent(KIMI_CHAT_URL, "Kilo-Code/1.0")
        candidates = _kimi_code_ua_candidates(KIMI_CHAT_URL)
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

        def fake_post(url, headers=None, **kwargs):
            calls.append(headers.get("User-Agent"))
            if headers.get("User-Agent") == KIMI_CODE_USER_AGENTS[0]:
                return _Resp(403, '{"error":{"type":"access_terminated_error"}}')
            return _Resp(200, "{}")

        monkeypatch.setattr(llm_core.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr("src.llm_core.httpx.post", fake_post)
        r = httpx_post_kimi_aware(KIMI_CHAT_URL, {"Authorization": "Bearer x"}, json={})
        assert r.status_code == 200
        assert calls[0] == KIMI_CODE_USER_AGENTS[0]
        assert calls[1] == KIMI_CODE_USER_AGENTS[1]
        _kimi_code_ua_cache.clear()

    @pytest.mark.asyncio
    async def test_async_post_uses_async_probe_not_sync_httpx_get(self, monkeypatch):
        _kimi_code_ua_cache.clear()

        class FakeClient:
            def __init__(self):
                self.get_user_agents = []
                self.post_user_agents = []

            async def get(self, url, headers=None, **kwargs):
                self.get_user_agents.append(headers.get("User-Agent"))
                if headers.get("User-Agent") == KIMI_CODE_USER_AGENTS[0]:
                    return _Resp(403, '{"error":{"type":"access_terminated_error"}}')
                return _Resp(200)

            async def post(self, url, headers=None, **kwargs):
                self.post_user_agents.append(headers.get("User-Agent"))
                return _Resp(200)

        def forbidden_sync_get(*args, **kwargs):
            raise AssertionError("async Kimi path must not call sync httpx.get")

        client = FakeClient()
        monkeypatch.setattr(llm_core.httpx, "get", forbidden_sync_get)

        r = await llm_core.httpx_post_kimi_aware_async(
            client,
            KIMI_CHAT_URL,
            {"Authorization": "Bearer x"},
            json={},
        )

        assert r.status_code == 200
        assert client.get_user_agents == [KIMI_CODE_USER_AGENTS[0], KIMI_CODE_USER_AGENTS[1]]
        assert client.post_user_agents == [KIMI_CODE_USER_AGENTS[1]]
        assert _kimi_code_ua_cache[_kimi_code_base_key(KIMI_CHAT_URL)] == KIMI_CODE_USER_AGENTS[1]
        _kimi_code_ua_cache.clear()

    @pytest.mark.asyncio
    async def test_async_post_preserves_fallback_when_probe_fails(self, monkeypatch):
        _kimi_code_ua_cache.clear()

        class FakeClient:
            def __init__(self):
                self.post_user_agents = []

            async def get(self, url, headers=None, **kwargs):
                raise RuntimeError("models probe unavailable")

            async def post(self, url, headers=None, **kwargs):
                self.post_user_agents.append(headers.get("User-Agent"))
                if headers.get("User-Agent") == KIMI_CODE_USER_AGENTS[0]:
                    return _Resp(403, '{"error":{"type":"access_terminated_error"}}')
                return _Resp(200)

        def forbidden_sync_get(*args, **kwargs):
            raise AssertionError("async Kimi path must not call sync httpx.get")

        client = FakeClient()
        monkeypatch.setattr(llm_core.httpx, "get", forbidden_sync_get)

        r = await llm_core.httpx_post_kimi_aware_async(
            client,
            KIMI_CHAT_URL,
            {"Authorization": "Bearer x"},
            json={},
        )

        assert r.status_code == 200
        assert client.post_user_agents == [KIMI_CODE_USER_AGENTS[0], KIMI_CODE_USER_AGENTS[1]]
        assert _kimi_code_ua_cache[_kimi_code_base_key(KIMI_CHAT_URL)] == KIMI_CODE_USER_AGENTS[1]
        _kimi_code_ua_cache.clear()

    @pytest.mark.asyncio
    async def test_stream_uses_async_kimi_probe_not_sync_httpx_get(self, monkeypatch):
        _kimi_code_ua_cache.clear()

        class FakeClient:
            def __init__(self):
                self.get_user_agents = []
                self.stream_headers = []

            async def get(self, url, headers=None, **kwargs):
                self.get_user_agents.append(headers.get("User-Agent"))
                if headers.get("User-Agent") == KIMI_CODE_USER_AGENTS[0]:
                    return _Resp(403, '{"error":{"type":"access_terminated_error"}}')
                return _Resp(200)

            def stream(self, method, url, **kwargs):
                self.stream_headers.append(kwargs.get("headers") or {})
                return _FakeStreamCtx(_FakeStreamResp(200))

        def forbidden_sync_get(*args, **kwargs):
            raise AssertionError("streaming Kimi path must not call sync httpx.get")

        client = FakeClient()
        monkeypatch.setattr(llm_core.httpx, "get", forbidden_sync_get)
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
        monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
        monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *args, **kwargs: None)

        chunks = [
            chunk
            async for chunk in llm_core.stream_llm(
                KIMI_CHAT_URL,
                "kimi-for-coding",
                [{"role": "user", "content": "hi"}],
                headers={"Authorization": "Bearer x"},
            )
        ]

        assert chunks == ["data: [DONE]\n\n"]
        assert client.get_user_agents == [KIMI_CODE_USER_AGENTS[0], KIMI_CODE_USER_AGENTS[1]]
        assert client.stream_headers[0]["User-Agent"] == KIMI_CODE_USER_AGENTS[1]
        assert _kimi_code_ua_cache[_kimi_code_base_key(KIMI_CHAT_URL)] == KIMI_CODE_USER_AGENTS[1]
        _kimi_code_ua_cache.clear()
