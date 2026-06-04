import json

import pytest

from src import codex_runtime, llm_core


@pytest.mark.asyncio
async def test_stream_llm_delegates_codex_scheme_to_runtime(monkeypatch):
    captured = {}

    async def fake_stream_codex(model, messages, *, timeout=300):
        captured["model"] = model
        captured["messages"] = messages
        captured["timeout"] = timeout
        yield 'data: {"delta": "ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(codex_runtime, "stream_codex", fake_stream_codex)
    monkeypatch.setattr(
        llm_core,
        "_get_http_client",
        lambda: (_ for _ in ()).throw(AssertionError("HTTP client should not be used")),
    )

    chunks = [
        chunk
        async for chunk in llm_core.stream_llm(
            "codex://runtime/chat/completions",
            "gpt-5.5",
            [
                {"role": "system", "content": "one"},
                {"role": "system", "content": "two"},
                {"role": "user", "content": "hello"},
            ],
            timeout=11,
            tools=[{"type": "function", "function": {"name": "bash"}}],
        )
    ]

    assert chunks == ['data: {"delta": "ok"}\n\n', "data: [DONE]\n\n"]
    assert captured["model"] == "gpt-5.5"
    assert captured["timeout"] == 11
    assert captured["messages"][0] == {"role": "system", "content": "one\n\ntwo"}
    assert captured["messages"][1] == {"role": "user", "content": "hello"}


@pytest.mark.asyncio
async def test_llm_call_async_delegates_codex_scheme_to_runtime(monkeypatch):
    captured = {}

    async def fake_call_codex(model, messages, *, timeout=300):
        captured["model"] = model
        captured["messages"] = messages
        captured["timeout"] = timeout
        return "done"

    monkeypatch.setattr(codex_runtime, "call_codex", fake_call_codex)
    monkeypatch.setattr(
        llm_core,
        "_get_http_client",
        lambda: (_ for _ in ()).throw(AssertionError("HTTP client should not be used")),
    )

    result = await llm_core.llm_call_async(
        "codex://runtime/chat/completions",
        "gpt-5.4-mini",
        [{"role": "user", "content": "hello"}],
        timeout=9,
    )

    assert result == "done"
    assert captured == {
        "model": "gpt-5.4-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "timeout": 9,
    }


@pytest.mark.asyncio
async def test_call_codex_raises_http_exception_on_error(monkeypatch):
    async def fake_stream_codex(model, messages, *, timeout=300):
        yield f"event: error\ndata: {json.dumps({'error': 'not logged in', 'status': 401})}\n\n"

    monkeypatch.setattr(codex_runtime, "stream_codex", fake_stream_codex)

    with pytest.raises(Exception) as exc_info:
        await codex_runtime.call_codex("gpt-5.5", [{"role": "user", "content": "hello"}])

    assert getattr(exc_info.value, "status_code", None) == 401
