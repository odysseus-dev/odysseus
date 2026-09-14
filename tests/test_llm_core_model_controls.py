"""Tests for capability-backed reasoning-effort and verbosity controls."""

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
    async def __aenter__(self):
        return _FakeResp()

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self):
        self.captured_payload = {}

    def stream(self, method, url, **kwargs):
        self.captured_payload = kwargs.get("json") or {}
        return _FakeStreamCtx()


def _record(*, reasoning=(), verbosity=(), status="claimed"):
    controls = []
    if reasoning:
        controls.append({
            "control": "reasoning_effort",
            "status": status,
            "source": "provider_reader",
            "confidence": "provider_reported",
            "evidence": {"allowed_values": list(reasoning)},
        })
    if verbosity:
        controls.append({
            "control": "verbosity",
            "status": status,
            "source": "provider_reader",
            "confidence": "provider_reported",
            "evidence": {"allowed_values": list(verbosity)},
        })
    return {"model_id": "opaque-model", "deterministic_controls": controls}


def _chatgpt_payload(capability, **controls):
    return llm_core._build_chatgpt_responses_payload(
        "opaque-model",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        model_capability=capability,
        **controls,
    )


def test_chatgpt_payload_sends_only_provider_advertised_values():
    capability = _record(
        reasoning=("low", "high", "xhigh"),
        verbosity=("low", "medium", "high"),
    )

    payload = _chatgpt_payload(capability, reasoning_effort="xhigh", verbosity="low")

    assert payload["reasoning"] == {"effort": "xhigh"}
    assert payload["text"] == {"verbosity": "low"}


def test_chatgpt_payload_omits_unadvertised_values_even_for_suggestive_model_name():
    capability = _record(reasoning=("low",), verbosity=("low",))

    payload = llm_core._build_chatgpt_responses_payload(
        "gpt-5.6-reasoning-max",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        model_capability=capability,
        reasoning_effort="max",
        verbosity="high",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_chatgpt_payload_unknown_or_missing_evidence_is_conservative():
    unknown = _chatgpt_payload(
        _record(reasoning=("high",), verbosity=("high",), status="unknown"),
        reasoning_effort="high",
        verbosity="high",
    )
    missing = _chatgpt_payload(None, reasoning_effort="high", verbosity="high")

    assert "reasoning" not in unknown and "text" not in unknown
    assert "reasoning" not in missing and "text" not in missing


def test_chatgpt_payload_rejects_evidence_for_a_different_model():
    capability = _record(reasoning=("high",))
    capability["model_id"] = "different-model"

    payload = _chatgpt_payload(capability, reasoning_effort="high")

    assert "reasoning" not in payload


def test_chatgpt_payload_maps_ui_off_only_when_provider_advertises_none():
    supported = _chatgpt_payload(_record(reasoning=("none", "high")), reasoning_effort="off")
    unsupported = _chatgpt_payload(_record(reasoning=("high",)), reasoning_effort="off")

    assert supported["reasoning"] == {"effort": "none"}
    assert "reasoning" not in unsupported


def test_auto_omits_controls_even_when_supported():
    payload = _chatgpt_payload(
        _record(reasoning=("low", "high"), verbosity=("low", "high")),
        reasoning_effort="auto",
        verbosity="auto",
    )

    assert "reasoning" not in payload
    assert "text" not in payload


def test_ollama_native_does_not_receive_user_control_without_exact_evidence():
    payload = llm_core._build_ollama_payload(
        "qwen3:14b",
        [{"role": "user", "content": "Hi"}],
        temperature=0.2,
        max_tokens=0,
        reasoning_effort="high",
    )

    assert "think" not in payload


def test_ollama_openai_compat_keeps_preexisting_tool_safe_default(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda _url, _model: 32768)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            "http://127.0.0.1:11434/v1/chat/completions",
            "qwen3:14b",
            [{"role": "user", "content": "Hi"}],
            reasoning_effort="high",
        )]

    asyncio.run(run())

    assert client.captured_payload["think"] is False


def test_response_cache_key_includes_model_controls():
    messages = [{"role": "user", "content": "Hi"}]
    low = llm_core._get_cache_key(
        "https://example.test/v1",
        "opaque-model",
        messages,
        0.2,
        100,
        reasoning_effort="low",
        verbosity="low",
    )
    high = llm_core._get_cache_key(
        "https://example.test/v1",
        "opaque-model",
        messages,
        0.2,
        100,
        reasoning_effort="high",
        verbosity="high",
    )

    assert low != high


def test_fallback_threads_exact_endpoint_identity_to_request_shaping(monkeypatch):
    captured = {}

    async def fake_stream(url, model, messages, **kwargs):
        captured.update(kwargs)
        yield 'data: {"delta":"ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm_with_fallback(
            [("https://chatgpt.com/backend-api/codex/responses", "opaque-model", {})],
            [{"role": "user", "content": "Hi"}],
            candidate_route_descriptors=[{"endpoint_id": "endpoint-exact"}],
        )]

    asyncio.run(run())

    assert captured["endpoint_id"] == "endpoint-exact"
