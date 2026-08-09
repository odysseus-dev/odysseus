"""Tests for optional reasoning-effort and verbosity request controls."""

import asyncio

from src import llm_core


class _FakeResp:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}'
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _FakeJsonResp:
    status_code = 200
    is_success = True
    content = b'{}'
    text = "{}"

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}

    async def aread(self):
        return self.content


class _FakeStreamCtx:
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return _FakeResp()

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self):
        self.captured_payload = {}

    def stream(self, method, url, **kwargs):
        self.captured_payload = kwargs.get("json") or {}
        return _FakeStreamCtx(self.captured_payload)

    async def post(self, url, **kwargs):
        self.captured_payload = kwargs.get("json") or {}
        return _FakeJsonResp()


def _capture_stream_payload(monkeypatch, url, model, **kwargs):
    client = _FakeClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda u, m: 32768)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            url,
            model,
            [{"role": "user", "content": "Hi"}],
            **kwargs,
        )]

    asyncio.run(run())
    return client.captured_payload


def _capture_async_payload(monkeypatch, url, model, **kwargs):
    client = _FakeClient()
    llm_core._response_cache.clear()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda _url, _model: 32768)

    asyncio.run(llm_core.llm_call_async(
        url,
        model,
        [{"role": "user", "content": "Hi"}],
        max_retries=1,
        **kwargs,
    ))
    return client.captured_payload


def _capture_sync_payload(monkeypatch, url, model, **kwargs):
    captured = {}
    llm_core._response_cache.clear()

    def fake_post(_url, _headers, **request_kwargs):
        captured.update(request_kwargs.get("json") or {})
        return _FakeJsonResp()

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware", fake_post)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda _url, _model: 32768)
    llm_core.llm_call(
        url,
        model,
        [{"role": "user", "content": "Hi"}],
        **kwargs,
    )
    return captured


def test_chatgpt_subscription_payload_adds_supported_controls():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
        verbosity="low",
    )

    assert payload["reasoning"] == {"effort": "high"}
    assert payload["text"] == {"verbosity": "low"}


def test_chatgpt_subscription_payload_maps_off_to_none_for_newer_gpt5():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.5",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="off",
    )

    assert payload["reasoning"] == {"effort": "none"}


def test_chatgpt_subscription_payload_omits_none_for_pre_5_1_gpt5():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="off",
    )

    assert "reasoning" not in payload


def test_chatgpt_subscription_payload_auto_omits_controls():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="auto",
        verbosity="auto",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_chatgpt_subscription_payload_unsupported_model_omits_controls():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-4o",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
        verbosity="high",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_chatgpt_subscription_payload_o_series_omits_minimal_reasoning():
    payload = llm_core._build_chatgpt_responses_payload(
        "o3-mini",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="minimal",
        verbosity="high",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_chatgpt_subscription_payload_o_series_accepts_high_reasoning():
    payload = llm_core._build_chatgpt_responses_payload(
        "o3-mini",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
    )

    assert payload["reasoning"] == {"effort": "high"}


def test_chatgpt_subscription_payload_invalid_values_omit_controls():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="maximum",
        verbosity="long",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_chatgpt_subscription_gpt_51_omits_unsupported_minimal():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.1-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="minimal",
    )

    assert "reasoning" not in payload


def test_chatgpt_subscription_gpt_52_supports_xhigh_but_not_minimal():
    xhigh = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="xhigh",
    )
    minimal = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="minimal",
    )

    assert xhigh["reasoning"] == {"effort": "xhigh"}
    assert "reasoning" not in minimal


def test_chatgpt_subscription_gpt_56_supports_max():
    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.6-sol",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="max",
    )

    assert payload["reasoning"] == {"effort": "max"}


def test_chatgpt_subscription_gpt5_pro_only_accepts_high():
    low = llm_core._build_chatgpt_responses_payload(
        "gpt-5-pro",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="low",
    )
    high = llm_core._build_chatgpt_responses_payload(
        "gpt-5-pro",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
    )

    assert "reasoning" not in low
    assert high["reasoning"] == {"effort": "high"}


def test_chatgpt_subscription_versioned_pro_uses_its_documented_levels():
    medium = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2-pro",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="medium",
    )
    low = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2-pro",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="low",
    )

    assert medium["reasoning"] == {"effort": "medium"}
    assert "reasoning" not in low


def test_chatgpt_subscription_codex_variants_do_not_inherit_none():
    off = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="off",
    )
    xhigh = llm_core._build_chatgpt_responses_payload(
        "gpt-5.2-codex",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="xhigh",
    )

    assert "reasoning" not in off
    assert xhigh["reasoning"] == {"effort": "xhigh"}


def test_ollama_native_reasoning_control_maps_to_binary_think():
    messages = [{"role": "user", "content": "Hi"}]

    auto_payload = llm_core._build_ollama_payload(
        "qwen3:14b",
        messages,
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="auto",
    )
    off_payload = llm_core._build_ollama_payload(
        "qwen3:14b",
        messages,
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="off",
    )
    on_payload = llm_core._build_ollama_payload(
        "qwen3:14b",
        messages,
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
    )

    assert "think" not in auto_payload
    assert off_payload["think"] is False
    assert on_payload["think"] is True


def test_ollama_gpt_oss_uses_thinking_levels_and_cannot_be_disabled():
    messages = [{"role": "user", "content": "Hi"}]
    high = llm_core._build_ollama_payload(
        "gpt-oss:20b",
        messages,
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
    )
    off = llm_core._build_ollama_payload(
        "gpt-oss:20b",
        messages,
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="off",
    )

    assert high["think"] == "high"
    assert "think" not in off


def test_generic_openai_compatible_endpoint_does_not_receive_controls(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "https://api.openai.com/v1/chat/completions",
        "gpt-5.1-codex",
        reasoning_effort="high",
        verbosity="high",
    )

    assert payload["model"] == "gpt-5.1-codex"
    assert "reasoning" not in payload
    assert "text" not in payload
    assert "reasoning_effort" not in payload
    assert "verbosity" not in payload


def test_ollama_openai_compat_auto_preserves_think_false(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "http://127.0.0.1:11434/v1/chat/completions",
        "qwen3:14b",
        reasoning_effort="auto",
    )

    assert payload["think"] is False


def test_ollama_openai_compat_explicit_reasoning_enables_think(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "http://127.0.0.1:11434/v1/chat/completions",
        "qwen3:14b",
        reasoning_effort="high",
    )

    assert payload["think"] is True


def test_ollama_openai_compat_off_disables_think(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "http://127.0.0.1:11434/v1/chat/completions",
        "qwen3:14b",
        reasoning_effort="off",
    )

    assert payload["think"] is False


def test_ollama_openai_compat_non_thinking_model_omits_think(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "http://127.0.0.1:11434/v1/chat/completions",
        "llama3.2:3b",
        reasoning_effort="high",
    )

    assert "think" not in payload


def test_ollama_openai_compat_gpt_oss_uses_level_in_stream(monkeypatch):
    payload = _capture_stream_payload(
        monkeypatch,
        "http://localhost:12345/v1/chat/completions",
        "gpt-oss:20b",
        reasoning_effort="medium",
    )

    assert payload["think"] == "medium"


def test_ollama_openai_compat_controls_apply_to_async_nonstream(monkeypatch):
    payload = _capture_async_payload(
        monkeypatch,
        "http://localhost:12345/v1/chat/completions",
        "qwen3:14b",
        reasoning_effort="high",
    )

    assert payload["think"] is True


def test_ollama_openai_compat_controls_apply_to_sync_nonstream(monkeypatch):
    payload = _capture_sync_payload(
        monkeypatch,
        "http://localhost:12345/v1/chat/completions",
        "qwen3:14b",
        reasoning_effort="off",
    )

    assert payload["think"] is False


def test_response_cache_key_includes_model_controls():
    messages = [{"role": "user", "content": "Hi"}]
    low = llm_core._get_cache_key(
        "https://example.test/v1",
        "gpt-5.2",
        messages,
        0.2,
        100,
        reasoning_effort="low",
        verbosity="low",
    )
    high = llm_core._get_cache_key(
        "https://example.test/v1",
        "gpt-5.2",
        messages,
        0.2,
        100,
        reasoning_effort="high",
        verbosity="high",
    )

    assert low != high
