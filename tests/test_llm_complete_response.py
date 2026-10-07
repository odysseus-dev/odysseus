"""Exercise the opt-in final-answer contract through real httpx transport."""

import json

import httpx
import pytest
from fastapi import HTTPException

from src import llm_core


@pytest.fixture
async def upstream(monkeypatch):
    responses = []
    requests = []

    async def handle(request):
        requests.append(request)
        assert responses, "Unexpected upstream request"
        response = responses.pop(0)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        yield responses, requests


def _completion(content, *, reasoning=None, finish_reason="stop"):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "model": "provider-reported-model",
        "choices": [{"message": message, "finish_reason": finish_reason}],
    }


def _responses_stream(*events):
    body = "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        for event in events
    )
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _response_delta(text):
    return {"type": "response.output_text.delta", "delta": text}


def _response_completed(**response):
    return {
        "type": "response.completed",
        "response": {"status": "completed", "model": "provider-reported-model", **response},
    }


_SUBSCRIPTION_URL = "https://chatgpt.com/backend-api/codex"


async def _call(**kwargs):
    options = {
        "url": "https://llm.test/v1/chat/completions",
        "model": "qwen3.5-9b",
        "messages": [{"role": "user", "content": "Describe the writing style."}],
        "temperature": 0.3,
        "max_tokens": 8192,
        "max_retries": 1,
    }
    options.update(kwargs)
    return await llm_core.llm_call_async(**options)


async def test_complete_answer_preserves_model_metadata_and_request(upstream):
    responses, requests = upstream
    responses.append(_completion("Write concise, friendly emails.", reasoning="analysis"))

    result = await _call(require_complete_response=True, return_model_metadata=True)

    assert result == ("Write concise, friendly emails.", "provider-reported-model")
    payload = json.loads(requests[0].content)
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 8192
    assert "require_complete_response" not in payload


@pytest.mark.parametrize("content", ["", None, " \n\t"])
async def test_reasoning_cannot_replace_empty_final_answer(upstream, content):
    responses, requests = upstream
    responses.extend([
        _completion(content, reasoning="Still analyzing the samples."),
        _completion("Write direct, polite emails."),
    ])

    with pytest.raises(HTTPException) as error:
        await _call(require_complete_response=True)

    assert error.value.status_code == 502
    assert error.value.detail == "LLM did not return a final answer"
    assert "analyzing" not in error.value.detail
    # A failed completion must not poison later attempts with cached reasoning.
    assert await _call(require_complete_response=True) == "Write direct, polite emails."
    assert len(requests) == 2


async def test_truncated_answer_is_rejected_even_when_final_text_exists(upstream):
    responses, _ = upstream
    responses.append(_completion("Write emails in this", finish_reason="length"))

    with pytest.raises(HTTPException) as error:
        await _call(require_complete_response=True)

    assert error.value.status_code == 502
    assert "output token limit" in error.value.detail
    assert not llm_core._response_cache


async def test_complete_cache_is_separate_from_default_reasoning_fallback(upstream):
    responses, requests = upstream
    responses.extend([
        _completion("", reasoning="Unfinished analysis", finish_reason="length"),
        _completion("Write concise emails."),
    ])

    # Existing callers retain their reasoning fallback, including its cache.
    assert await _call() == "Unfinished analysis"
    assert await _call(require_complete_response=True) == "Write concise emails."
    assert await _call() == "Unfinished analysis"
    assert await _call(require_complete_response=True, return_model_metadata=True) == (
        "Write concise emails.", "provider-reported-model",
    )
    assert len(requests) == 2


async def test_default_call_still_returns_partial_final_text(upstream):
    responses, _ = upstream
    responses.append(_completion("Partial final answer", finish_reason="length"))

    assert await _call() == "Partial final answer"


async def test_complete_mistral_answer_excludes_structured_thinking(upstream):
    responses, requests = upstream
    content = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "Private analysis"}]},
        {"type": "text", "text": "Write clear, "},
        {"type": "text", "text": "friendly emails."},
    ]
    responses.extend([_completion(content), _completion(content)])

    assert await _call(require_complete_response=True) == "Write clear, friendly emails."
    assert await _call() == "Private analysis\n\nWrite clear, friendly emails."
    assert len(requests) == 2


async def test_structured_thinking_without_final_text_is_rejected(upstream):
    responses, _ = upstream
    responses.append(_completion([
        {"type": "thinking", "thinking": [{"type": "text", "text": "Still thinking"}]},
    ]))

    with pytest.raises(HTTPException, match="final answer"):
        await _call(require_complete_response=True)


@pytest.mark.parametrize("url,payload", [
    (
        "https://ollama.com/api/chat",
        {"message": {"content": "Partial style"}, "done": True, "done_reason": "length"},
    ),
    (
        "https://api.anthropic.com/v1/messages",
        {"content": [{"type": "text", "text": "Partial style"}], "stop_reason": "max_tokens"},
    ),
])
async def test_native_provider_token_limits_are_rejected(upstream, monkeypatch, url, payload):
    responses, _ = upstream
    # Ollama context discovery is outside the completion request being tested.
    monkeypatch.setattr(llm_core, "get_context_length", lambda *args: llm_core.DEFAULT_CONTEXT)
    responses.append(payload)

    with pytest.raises(HTTPException) as error:
        await _call(url=url, require_complete_response=True)

    assert error.value.status_code == 502
    assert "output token limit" in error.value.detail


@pytest.mark.parametrize("url,payload", [
    (
        "https://ollama.com/api/chat",
        {"message": {"content": "Finished style", "thinking": "analysis"}, "done_reason": "stop"},
    ),
    (
        "https://api.anthropic.com/v1/messages",
        {
            "content": [
                {"type": "thinking", "thinking": "analysis"},
                {"type": "text", "text": "Finished "},
                {"type": "text", "text": "style"},
            ],
            "stop_reason": "end_turn",
        },
    ),
])
async def test_native_provider_final_text_remains_supported(upstream, monkeypatch, url, payload):
    responses, _ = upstream
    monkeypatch.setattr(llm_core, "get_context_length", lambda *args: llm_core.DEFAULT_CONTEXT)
    responses.append(payload)

    assert await _call(url=url, require_complete_response=True) == "Finished style"


async def test_subscription_complete_answer_and_model_are_cached(upstream):
    responses, requests = upstream
    responses.append(_responses_stream(
        _response_delta("Write clear, "),
        _response_delta("friendly emails."),
        _response_completed(usage={"input_tokens": 10, "output_tokens": 8}),
    ))

    result = await _call(
        url=_SUBSCRIPTION_URL, require_complete_response=True, return_model_metadata=True,
    )

    assert result == ("Write clear, friendly emails.", "provider-reported-model")
    assert await _call(url=_SUBSCRIPTION_URL, require_complete_response=True) == result[0]
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["stream"] is True
    assert "require_complete_response" not in payload


@pytest.mark.parametrize("terminal", [
    {"type": "response.incomplete", "response": {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
    }},
    _response_completed(status="incomplete"),
    _response_completed(incomplete_details={"reason": "max_output_tokens"}),
    _response_completed(error={"message": "Generation failed"}),
    {"type": "response.failed", "response": {
        "error": {"message": "Generation failed", "status": 502},
    }},
    {"type": "error", "message": "Generation failed", "status": 502},
])
async def test_subscription_reported_failure_rejects_partial_text_without_caching(upstream, terminal):
    responses, requests = upstream
    responses.extend([
        _responses_stream(_response_delta("Unfinished style"), terminal),
        _responses_stream(_response_delta("Finished style"), _response_completed()),
    ])

    with pytest.raises(HTTPException) as error:
        await _call(url=_SUBSCRIPTION_URL, require_complete_response=True)

    assert error.value.status_code == 502
    assert not llm_core._response_cache
    assert not llm_core._response_model_cache
    assert await _call(url=_SUBSCRIPTION_URL, require_complete_response=True) == "Finished style"
    assert len(requests) == 2


@pytest.mark.parametrize("trailing_done", [False, True])
async def test_subscription_eof_without_completion_rejects_partial_text(upstream, trailing_done):
    responses, _ = upstream
    response = _responses_stream(_response_delta("Unfinished style"))
    if trailing_done:
        response = httpx.Response(200, content=response.content + b"data: [DONE]\n\n")
    responses.append(response)

    with pytest.raises(HTTPException) as error:
        await _call(url=_SUBSCRIPTION_URL, require_complete_response=True)

    assert error.value.status_code == 502
    assert "before completing" in error.value.detail
    assert not llm_core._response_cache


@pytest.mark.parametrize("failure,status", [
    (httpx.RemoteProtocolError("Synthetic disconnect"), 502),
    (httpx.ReadTimeout("Synthetic timeout"), 504),
])
async def test_subscription_interrupted_transport_rejects_partial_text(upstream, failure, status):
    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _responses_stream(_response_delta("Unfinished style")).content
            raise failure

    responses, _ = upstream
    responses.append(httpx.Response(200, stream=InterruptedStream()))

    with pytest.raises(HTTPException) as error:
        await _call(url=_SUBSCRIPTION_URL, require_complete_response=True)

    assert error.value.status_code == status
    assert not llm_core._response_cache
    assert not llm_core._response_model_cache


@pytest.mark.parametrize("text", ["", " \n\t"])
async def test_subscription_completed_without_final_answer_is_rejected(upstream, text):
    responses, _ = upstream
    responses.append(_responses_stream(_response_delta(text), _response_completed()))

    with pytest.raises(HTTPException, match="final answer"):
        await _call(url=_SUBSCRIPTION_URL, require_complete_response=True)

    assert not llm_core._response_cache


@pytest.mark.parametrize("terminal", [None, {
    "type": "response.incomplete", "response": {"status": "incomplete"},
}])
async def test_default_subscription_call_still_returns_partial_text(upstream, terminal):
    responses, requests = upstream
    events = [_response_delta("Partial style")]
    if terminal:
        events.append(terminal)
    responses.extend([
        _responses_stream(*events),
        _responses_stream(_response_delta("Finished style"), _response_completed()),
    ])

    assert await _call(url=_SUBSCRIPTION_URL) == "Partial style"
    assert await _call(url=_SUBSCRIPTION_URL, require_complete_response=True) == "Finished style"
    assert await _call(url=_SUBSCRIPTION_URL) == "Partial style"
    assert len(requests) == 2


@pytest.mark.parametrize("require_complete_response", [False, True])
async def test_subscription_completion_confirmation_is_opt_in(upstream, require_complete_response):
    responses, _ = upstream
    responses.append(_responses_stream(_response_delta("Finished style"), _response_completed()))

    chunks = [chunk async for chunk in llm_core.stream_llm(
        _SUBSCRIPTION_URL, "qwen3.5-9b", [{"role": "user", "content": "Describe the style."}],
        require_complete_response=require_complete_response,
    )]

    assert any('"type": "response_complete"' in chunk for chunk in chunks) is require_complete_response
    assert chunks[-1] == "data: [DONE]\n\n"


async def test_subscription_done_without_opt_in_confirmation_is_rejected(upstream, monkeypatch):
    async def interrupted_stream(*args, **kwargs):
        assert kwargs["require_complete_response"] is True
        yield 'data: {"delta": "Partial style"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", interrupted_stream)

    with pytest.raises(HTTPException, match="before completing"):
        await _call(url=_SUBSCRIPTION_URL, require_complete_response=True)

    assert not llm_core._response_cache


def _native_answer_stream(provider, *, answer="Finished email reply.", truncated=False, interrupted=False):
    if provider == "ollama":
        frames = [{"message": {"content": answer}}]
        if not interrupted:
            frames.append({"done": True, "done_reason": "length" if truncated else "stop"})
        body = "".join(json.dumps(frame) + "\n" for frame in frames)
    elif provider == "anthropic":
        frames = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": answer}}]
        if not interrupted:
            frames.extend([
                {"type": "message_delta", "delta": {"stop_reason": "max_tokens" if truncated else "end_turn"}},
                {"type": "message_stop"},
            ])
        body = "".join("data:" + json.dumps(frame) + "\n\n" for frame in frames)
    else:
        frames = [{"choices": [{"delta": {"content": answer}}]}]
        if not interrupted:
            frames.append({"choices": [{"delta": {}, "finish_reason": "length" if truncated else "stop"}]})
        body = "".join("data:" + json.dumps(frame) + "\n\n" for frame in frames)
        if not interrupted:
            body += "data:[DONE]\n\n"
    return httpx.Response(200, text=body)


@pytest.mark.parametrize("provider,url", [
    ("compatible", "https://llm.test/v1"),
    ("ollama", "https://ollama.com/api/chat"),
    ("anthropic", "https://api.anthropic.com/v1/messages"),
])
@pytest.mark.parametrize("failure", [None, "truncated", "interrupted", "empty"])
async def test_strict_native_stream_requires_finished_nonempty_answer(upstream, provider, url, failure):
    responses, _ = upstream
    responses.append(_native_answer_stream(
        provider, answer="" if failure == "empty" else "Finished email reply.",
        truncated=failure == "truncated", interrupted=failure == "interrupted",
    ))
    chunks = [chunk async for chunk in llm_core.stream_llm(
        url, "fixture-model", [{"role": "user", "content": "Draft a reply."}],
        require_complete_response=True,
    )]
    output = "".join(chunks)
    if failure:
        assert "event: error" in output
        assert "data: [DONE]" not in output
    else:
        assert '"type": "response_complete"' in output
        assert "data: [DONE]" in output
        assert "event: error" not in output


@pytest.mark.parametrize("provider,url", [
    ("compatible", "https://llm.test/v1"),
    ("ollama", "https://ollama.com/api/chat"),
    ("anthropic", "https://api.anthropic.com/v1/messages"),
])
async def test_default_native_stream_contract_remains_unchanged(upstream, provider, url):
    responses, _ = upstream
    responses.append(_native_answer_stream(provider, truncated=True))
    output = "".join([chunk async for chunk in llm_core.stream_llm(
        url, "fixture-model", [{"role": "user", "content": "Draft a reply."}],
    )])
    assert "data: [DONE]" in output
    assert "response_complete" not in output
    assert "event: error" not in output


async def test_legacy_positional_async_controls_preserve_default_completion_contract(upstream, monkeypatch):
    responses, _ = upstream
    responses.append(_completion("", reasoning="Legacy reasoning fallback", finish_reason="length"))
    captured = []
    original_cache_key = llm_core._get_cache_key

    def capture_cache_key(*args, **kwargs):
        captured.append(kwargs)
        return original_cache_key(*args, **kwargs)

    monkeypatch.setattr(llm_core, "_get_cache_key", capture_cache_key)
    result = await llm_core.llm_call_async(
        "https://llm.test/v1", "fixture-model", [{"role": "user", "content": "Reply."}],
        0.3, 128, None, 60, 1, None, None, "foreground", False, False, "off", "low",
    )

    assert result == "Legacy reasoning fallback"
    assert captured[0]["thinking_mode"] == "off"
    assert captured[0]["reasoning_effort"] == "low"
    assert all(not key.startswith(("complete:", "thinking:")) for key in llm_core._response_cache)


async def test_legacy_positional_stream_controls_preserve_default_frames_and_inner_retry(upstream, monkeypatch):
    import inspect

    responses, _ = upstream
    responses.extend([_native_answer_stream("compatible", truncated=True) for _ in range(2)])
    captured = []
    original_inner = llm_core._stream_llm_inner

    async def capture_inner(*args, **kwargs):
        binding = inspect.signature(original_inner).bind(*args, **kwargs)
        binding.apply_defaults()
        captured.append(binding.arguments)
        async for chunk in original_inner(*args, **kwargs):
            yield chunk

    monkeypatch.setattr(llm_core, "_stream_llm_inner", capture_inner)
    arguments = (
        "https://llm.test/v1", "fixture-model", [{"role": "user", "content": "Reply."}],
        0.3, 128, None, 60, None, None, None, False, "foreground", "off", "low",
    )
    public_output = "".join([chunk async for chunk in llm_core.stream_llm(*arguments)])
    inner_arguments = (*arguments[:11], *arguments[12:], False)
    inner_output = "".join([chunk async for chunk in llm_core._stream_llm_inner(*inner_arguments)])

    for output in (public_output, inner_output):
        assert "Finished email reply." in output
        assert "data: [DONE]" in output
        assert "response_complete" not in output
        assert "event: error" not in output
    for binding in captured:
        assert binding["thinking_mode"] == "off"
        assert binding["reasoning_effort"] == "low"
        assert binding["require_complete_response"] is False
    assert captured[1]["_retry_silent_local"] is False
