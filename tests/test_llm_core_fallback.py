"""Tests for the fallback indicator in stream_llm_with_fallback.

When the selected model fails *before output* and another candidate answers,
a `fallback` event must be emitted so the switch is never masked under the
selected model's name (which is how a misconfigured provider can look like it
works while a different model silently answers).
"""
import json
import asyncio

import httpx
import pytest
from fastapi import HTTPException

from src import llm_core


class _ProviderResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _ProviderStreamContext:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _ProviderResponse(self._lines)

    async def __aexit__(self, *args):
        return False


class _ProviderClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kwargs):
        return _ProviderStreamContext(self._lines)


def _run_fallback(monkeypatch, per_model, **fallback_kwargs):
    """Drive stream_llm_with_fallback with a stubbed stream_llm that returns a
    canned SSE line list per candidate model. Returns the emitted chunks."""
    async def fake_stream(url, model, messages, **kw):
        for ln in per_model(model):
            yield ln
    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        out = []
        async for c in llm_core.stream_llm_with_fallback(
            [("u1", "primary", {}), ("u2", "backup", {})],
            [{"role": "user", "content": "hi"}],
            **fallback_kwargs,
        ):
            out.append(c)
        return out

    return asyncio.run(run())


def _run_provider_stream(monkeypatch, url, lines):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _ProviderClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)

    async def run():
        return [
            chunk
            async for chunk in llm_core._stream_llm_inner(
                url,
                "configured-model",
                [{"role": "user", "content": "hi"}],
                headers={"Authorization": "Bearer test"},
            )
        ]

    return asyncio.run(run())


def test_fallback_emits_indicator_when_primary_fails(monkeypatch):
    def per_model(model):
        if model == "primary":
            return ['event: error\ndata: {"status": 400, "text": "Provider X returned HTTP 400"}\n\n']
        return ['data: {"delta": "hello"}\n\n', "data: [DONE]\n\n"]
    chunks = _run_fallback(monkeypatch, per_model)
    fb = [json.loads(c[6:]) for c in chunks if c.startswith("data: ") and '"fallback"' in c]
    assert fb, f"no fallback event in {chunks}"
    assert fb[0]["type"] == "fallback"
    assert fb[0]["selected_model"] == "primary"
    assert fb[0]["answered_by"] == "backup"
    assert fb[0]["candidate_index"] == 1
    assert "400" in fb[0]["reason"]
    # the fallback notice must precede the answer content
    order = [i for i, c in enumerate(chunks) if '"fallback"' in c or '"delta": "hello"' in c]
    assert order == sorted(order)
    assert any('"delta": "hello"' in c for c in chunks)


def test_no_fallback_event_when_primary_succeeds(monkeypatch):
    def per_model(model):
        return ['data: {"delta": "ok"}\n\n', "data: [DONE]\n\n"]
    chunks = _run_fallback(monkeypatch, per_model)
    assert not any('"fallback"' in c for c in chunks)


def test_done_only_primary_invokes_fallback(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return ["data: [DONE]\n\n"]
        return [
            'data: {"type": "model_actual", "requested_model": "backup", "model": "backup-v2"}\n\n',
            'data: {"delta": "backup answer"}\n\n',
            "data: [DONE]\n\n",
        ]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    assert any('"delta": "backup answer"' in c for c in chunks)
    model_idx = next(i for i, c in enumerate(chunks) if '"model_actual"' in c)
    fallback_idx = next(i for i, c in enumerate(chunks) if '"fallback"' in c)
    answer_idx = next(i for i, c in enumerate(chunks) if '"delta": "backup answer"' in c)
    assert fallback_idx < model_idx < answer_idx


def test_usage_then_done_primary_invokes_fallback_and_discards_usage(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return [
                'data: {"type": "usage", "data": {"input_tokens": 4, "output_tokens": 0}}\n\n',
                "data: [DONE]\n\n",
            ]
        return ['data: {"delta": "backup answer"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    assert not any('"type": "usage"' in c for c in chunks)


@pytest.mark.parametrize(
    "output_chunk",
    [
        'data: {"delta": "visible text"}\n\n',
        'data: {"delta": "reasoning", "thinking": true}\n\n',
    ],
)
def test_text_or_reasoning_output_prevents_fallback(monkeypatch, output_chunk):
    calls = []

    def per_model(model):
        calls.append(model)
        return [output_chunk, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary"]
    assert output_chunk in chunks
    assert not any('"fallback"' in c for c in chunks)


def test_foreground_whitespace_only_delta_surfaces_empty_response_without_fallback(monkeypatch):
    calls = []
    whitespace = 'data: {"delta": "   "}\n\n'

    def per_model(model):
        calls.append(model)
        return [whitespace, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model, fallback_on_empty=False)
    assert calls == ["primary"]
    assert whitespace not in chunks
    assert not any('"fallback"' in c for c in chunks)
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert "returned no substantive output" in chunks[0]


def test_completed_tool_call_output_prevents_fallback(monkeypatch):
    calls = []
    tool_calls = 'data: {"type": "tool_calls", "calls": [{"id": "c1", "name": "bash", "arguments": "{}"}]}\n\n'

    def per_model(model):
        calls.append(model)
        return [tool_calls, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary"]
    assert tool_calls in chunks
    assert not any('"fallback"' in c for c in chunks)


def test_tool_call_delta_is_released_only_after_completed_call_commits_route(monkeypatch):
    calls = []
    advanced_past_delta = False
    tool_delta = 'data: {"type": "tool_call_delta", "index": 0, "arg_delta": "{\\"path\\":"}\n\n'
    tool_calls = 'data: {"type": "tool_calls", "calls": [{"id": "c1", "name": "write_file", "arguments": "{\\"path\\":\\"x\\"}"}]}\n\n'

    async def fake_stream(url, model, messages, **kw):
        nonlocal advanced_past_delta
        calls.append(model)
        yield tool_delta
        advanced_past_delta = True
        yield tool_calls
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        stream = llm_core.stream_llm_with_fallback(
            [("u1", "primary", {}), ("u2", "backup", {})],
            [{"role": "user", "content": "hi"}],
        )
        first = await anext(stream)
        assert first == tool_delta
        assert advanced_past_delta
        chunks = [first]
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())
    assert calls == ["primary"]
    assert tool_calls in chunks
    assert not any('"type": "fallback"' in c for c in chunks)


def test_incomplete_tool_call_delta_is_discarded_before_eligible_fallback(monkeypatch):
    calls = []
    tool_delta = 'data: {"type": "tool_call_delta", "index": 0, "arg_delta": "{\\"path\\":"}\n\n'
    terminal = 'event: error\ndata: {"status": 503, "error": "unavailable"}\n\n'

    async def fake_stream(url, model, messages, **kw):
        calls.append(model)
        if model == "primary":
            yield tool_delta
            yield terminal
            return
        yield 'data: {"delta": "backup answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={503},
            )
        ]

    chunks = asyncio.run(run())
    assert calls == ["primary", "backup"]
    assert tool_delta not in chunks
    assert any('"type": "fallback"' in chunk for chunk in chunks)
    assert any("backup answer" in chunk for chunk in chunks)


def test_empty_final_candidate_surfaces_terminal_error(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return []  # clean EOF without substantive output
        return ["data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    errors = [c for c in chunks if c.startswith("event: error")]
    assert len(errors) == 1
    assert "All model candidates returned no substantive output" in errors[0]
    assert '"status": 502' in errors[0]


def test_explicit_foreground_policy_falls_back_on_availability_error(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return ['event: error\ndata: {"status": 503, "text": "unavailable"}\n\n']
        return ['data: {"delta": "backup answer"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(
        monkeypatch,
        per_model,
        fallback_statuses={408, 425, 429, 500, 502, 503, 504, 507, 508, 529},
        fallback_on_empty=False,
    )

    assert calls == ["primary", "backup"]
    assert any('"type": "fallback"' in chunk for chunk in chunks)
    assert any('"delta": "backup answer"' in chunk for chunk in chunks)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_explicit_foreground_policy_does_not_fallback_on_request_errors(monkeypatch, status):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return [f'event: error\ndata: {{"status": {status}, "text": "request rejected"}}\n\n']
        return ['data: {"delta": "must not run"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(
        monkeypatch,
        per_model,
        fallback_statuses={408, 425, 429, 500, 502, 503, 504, 507, 508, 529},
        fallback_on_empty=False,
    )

    assert calls == ["primary"]
    assert chunks == [f'event: error\ndata: {{"status": {status}, "text": "request rejected"}}\n\n']


def test_explicit_foreground_policy_does_not_fallback_on_empty_completion(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        return ["data: [DONE]\n\n"]

    chunks = _run_fallback(
        monkeypatch,
        per_model,
        fallback_statuses={408, 425, 429, 500, 502, 503, 504, 507, 508, 529},
        fallback_on_empty=False,
    )

    assert calls == ["primary"]
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert "returned no substantive output" in chunks[0]


def test_explicit_foreground_policy_respects_adapter_ineligible_override(monkeypatch):
    calls = []
    terminal = 'event: error\ndata: {"status": 502, "error": "local adapter failure", "fallback_eligible": false}\n\n'

    def per_model(model):
        calls.append(model)
        return [terminal] if model == "primary" else ['data: {"delta": "must not run"}\n\n']

    chunks = _run_fallback(
        monkeypatch,
        per_model,
        fallback_statuses={502},
        fallback_on_empty=False,
    )

    assert calls == ["primary"]
    assert chunks == [terminal]


def test_generic_policy_preserves_legacy_fallback_for_adapter_errors(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return ['event: error\ndata: {"status": 502, "fallback_eligible": false}\n\n']
        return ['data: {"delta": "legacy backup"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)

    assert calls == ["primary", "backup"]
    assert any('"delta": "legacy backup"' in chunk for chunk in chunks)


def test_candidate_request_factory_builds_each_attempt_before_streaming(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append((model, messages, kwargs.get("tools")))
        if model == "primary":
            yield 'event: error\ndata: {"status": 503, "text": "down"}\n\n'
        else:
            yield 'data: {"delta": "backup"}\n\n'
            yield "data: [DONE]\n\n"

    def request_factory(index, url, model, headers):
        return {
            "messages": [{"role": "user", "content": f"prompt for {model}"}],
            "kwargs": {"tools": [model] if index == 0 else None},
        }

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "shared"}],
                fallback_statuses={503},
                fallback_on_empty=False,
                candidate_request_factory=request_factory,
            )
        ]

    chunks = asyncio.run(run())

    assert calls == [
        ("primary", [{"role": "user", "content": "prompt for primary"}], ["primary"]),
        ("backup", [{"role": "user", "content": "prompt for backup"}], None),
    ]
    assert any('"delta": "backup"' in chunk for chunk in chunks)


def test_candidate_request_factory_eligible_failure_advances_streaming_route(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(("stream", model))
        yield 'data: {"delta": "backup"}\n\n'
        yield "data: [DONE]\n\n"

    async def request_factory(index, url, model, headers):
        calls.append(("factory", model))
        if model == "primary":
            raise HTTPException(503, "primary unavailable during compaction")
        return {"messages": [{"role": "user", "content": "backup prompt"}]}

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "shared"}],
                fallback_statuses={503},
                fallback_on_empty=False,
                candidate_request_factory=request_factory,
            )
        ]

    chunks = asyncio.run(run())

    assert calls == [
        ("factory", "primary"),
        ("factory", "backup"),
        ("stream", "backup"),
    ]
    assert any('"type": "fallback"' in chunk for chunk in chunks)
    assert any('"delta": "backup"' in chunk for chunk in chunks)


def test_candidate_request_factory_ineligible_failure_stops_streaming_route(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(("stream", model))
        yield 'data: {"delta": "must not run"}\n\n'

    async def request_factory(index, url, model, headers):
        calls.append(("factory", model))
        raise HTTPException(401, "invalid credentials")

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "shared"}],
                fallback_statuses={503},
                fallback_on_empty=False,
                candidate_request_factory=request_factory,
            )
        ]

    chunks = asyncio.run(run())

    assert calls == [("factory", "primary")]
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    assert '"status": 401' in chunks[0]
    assert "invalid credentials" not in chunks[0]


def test_response_cache_is_partitioned_by_non_secret_header_identity(monkeypatch):
    calls = []
    llm_core._response_cache.clear()

    def fake_post(url, headers=None, json=None, timeout=None):
        credential = headers.get("Authorization")
        calls.append(credential)
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": f"answer from {credential}"}}]},
        )

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)
    messages = [{"role": "user", "content": "same prompt"}]
    try:
        first = llm_core.llm_call(
            "https://same.example/v1",
            "same-model",
            messages,
            headers={"Authorization": "Bearer one"},
        )
        second = llm_core.llm_call(
            "https://same.example/v1",
            "same-model",
            messages,
            headers={"Authorization": "Bearer two"},
        )
        first_again = llm_core.llm_call(
            "https://same.example/v1",
            "same-model",
            messages,
            headers={"Authorization": "Bearer one"},
        )
    finally:
        llm_core._response_cache.clear()

    assert first == "answer from Bearer one"
    assert second == "answer from Bearer two"
    assert first_again == first
    assert calls == ["Bearer one", "Bearer two"]
    key = llm_core._get_cache_key(
        "https://same.example/v1",
        "same-model",
        messages,
        0.7,
        4096,
        headers={"Authorization": "Bearer one"},
    )
    assert "Bearer one" not in key


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        ({"type": "invalid_request_error", "code": "model_not_found", "message": "Unsupported model"}, 404),
        ({"type": "rate_limit_error", "message": "Too many requests"}, 429),
        ({"type": "server_error", "message": "Temporarily unavailable"}, 500),
        ({"status": True, "message": "rate limit"}, 400),
    ],
)
def test_chatgpt_subscription_stream_preserves_error_semantics(monkeypatch, error, expected_status):
    lines = [
        "event: response.failed",
        "data: " + json.dumps({"type": "response.failed", "response": {"error": error}}),
    ]

    chunks = _run_provider_stream(
        monkeypatch,
        "https://chatgpt.com/backend-api/codex/responses",
        lines,
    )

    assert len(chunks) == 1
    assert json.loads(chunks[0].split("data: ", 1)[1])["status"] == expected_status


def test_chatgpt_subscription_top_level_error_preserves_semantics(monkeypatch):
    chunks = _run_provider_stream(
        monkeypatch,
        "https://chatgpt.com/backend-api/codex/responses",
        ["data: " + json.dumps({
            "type": "error",
            "code": "server_error",
            "message": "Temporarily unavailable",
        })],
    )

    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload["status"] == 500
    assert payload["text"] == "Temporarily unavailable"


def test_provider_explicit_status_wins_over_transient_text():
    assert llm_core._provider_stream_error_status({
        "status": 401,
        "message": "Temporarily unavailable",
    }) == 401


@pytest.mark.parametrize(
    "symbolic_status",
    ["RATE_LIMITED", "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED"],
)
def test_provider_symbolic_rate_limited_status_is_availability_evidence(symbolic_status):
    assert llm_core._provider_stream_error_status({
        "status": symbolic_status,
        "message": "Request quota reached",
    }) == 429


def test_provider_unknown_symbolic_status_still_fails_closed():
    assert llm_core._provider_stream_error_status({
        "status": "PERMISSION_DENIED",
        "message": "denied",
    }) == 400


def test_provider_numeric_code_wins_over_symbolic_rate_limit_status():
    # Google-style payloads pair a symbolic status with a numeric code; the
    # numeric truth must surface instead of advancing fallback on the symbol.
    assert llm_core._provider_stream_error_status({
        "status": "RATE_LIMITED",
        "code": 401,
        "message": "bad key",
    }) == 401


@pytest.mark.parametrize("status", [True, 429.9, "429.0", float("inf")])
def test_provider_malformed_status_fails_closed(status):
    assert llm_core._provider_stream_error_status({
        "status": status,
        "message": "rate limit",
    }) == 400


def test_stream_fractional_status_does_not_advance_fallback(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return ['event: error\ndata: {"status": 429.9, "error": "malformed status"}\n\n']
        return ['data: {"delta": "backup"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(
        monkeypatch,
        per_model,
        fallback_statuses={429},
        fallback_on_empty=False,
    )

    assert calls == ["primary"]
    assert not any('"fallback"' in chunk for chunk in chunks)
    assert any(chunk.startswith("event: error") for chunk in chunks)


def test_failed_candidate_closes_before_next_route_starts(monkeypatch):
    state = {"primary_closed": False, "backup_started": False}

    async def fake_stream(url, model, messages, **kwargs):
        try:
            if model == "primary":
                yield 'event: error\ndata: {"status": 503, "error": "down"}\n\n'
                yield 'data: {"delta": "must not continue"}\n\n'
            else:
                state["backup_started"] = True
                assert state["primary_closed"] is True
                yield 'data: {"delta": "backup"}\n\n'
                yield "data: [DONE]\n\n"
        finally:
            if model == "primary":
                state["primary_closed"] = True

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={503},
                fallback_on_empty=False,
            )
        ]

    chunks = asyncio.run(run())

    assert state == {"primary_closed": True, "backup_started": True}
    assert any('"delta": "backup"' in chunk for chunk in chunks)


def test_consumer_close_closes_active_candidate_stream(monkeypatch):
    state = {"closed": False}

    async def fake_stream(url, model, messages, **kwargs):
        try:
            yield 'data: {"delta": "partial"}\n\n'
            yield 'data: {"delta": "more"}\n\n'
        finally:
            state["closed"] = True

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        stream = llm_core.stream_llm_with_fallback(
            [("u1", "primary", {})],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={503},
            fallback_on_empty=False,
        )
        assert '"delta": "partial"' in await anext(stream)
        await stream.aclose()

    asyncio.run(run())

    assert state["closed"] is True


def test_nonstream_fractional_status_does_not_advance_fallback(monkeypatch):
    calls = []

    class FractionalStatusError(Exception):
        status_code = 429.9

    async def fake_call(url, model, messages, **kwargs):
        calls.append(model)
        raise FractionalStatusError("malformed status")

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    with pytest.raises(FractionalStatusError):
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("https://selected.example/v1", "selected", {}),
                ("https://backup.example/v1", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={429},
        ))

    assert calls == ["selected"]


def test_nonstream_provider_boolean_status_does_not_advance_fallback(monkeypatch):
    calls = []

    async def fake_post(client, url, headers, **kwargs):
        calls.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"error": {"status": True, "message": "rate limit"}},
        )

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("https://selected.example/v1", "selected", {}),
                ("https://backup.example/v1", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={429},
        ))

    assert exc.value.status_code == 400
    assert calls == ["https://selected.example/v1/chat/completions"]


def test_google_style_numeric_error_code_is_classified_as_http_status():
    assert llm_core._provider_stream_error_status({
        "code": 429,
        "status": "RESOURCE_EXHAUSTED",
        "message": "Quota temporarily exhausted",
    }) == 429


def test_nonstream_model_metadata_round_trips_through_response_cache(monkeypatch):
    calls = []
    llm_core._response_cache.clear()
    llm_core._response_model_cache.clear()

    class _Response:
        is_success = True
        status_code = 200
        text = ""

        def json(self):
            return {
                "model": "provider-model-alias",
                "choices": [{"message": {"content": "answer"}}],
            }

    async def fake_post(_client, target_url, _headers, **kwargs):
        calls.append(target_url)
        return _Response()

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: object())
    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)

    async def run():
        kwargs = {
            "url": "https://selected.example/v1",
            "model": "selected-model",
            "messages": [{"role": "user", "content": "hello"}],
            "return_model_metadata": True,
        }
        first = await llm_core.llm_call_async(**kwargs)
        second = await llm_core.llm_call_async(**kwargs)
        return first, second

    try:
        first, second = asyncio.run(run())
    finally:
        llm_core._response_cache.clear()
        llm_core._response_model_cache.clear()

    assert first == second == ("answer", "provider-model-alias")
    assert calls == ["https://selected.example/v1/chat/completions"]


@pytest.mark.parametrize(
    ("url", "line"),
    [
        (
            "https://openai-compatible.example/v1",
            "data: " + json.dumps({"error": {"type": "rate_limit_error", "message": "Too many requests"}}),
        ),
        (
            "http://localhost:11434/api/chat",
            json.dumps({"error": {"type": "server_error", "message": "Temporarily unavailable"}}),
        ),
    ],
)
def test_stream_adapters_surface_top_level_provider_errors(monkeypatch, url, line):
    chunks = _run_provider_stream(monkeypatch, url, [line])

    assert len(chunks) == 1
    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload["status"] in {429, 500}


_ABSURD_USAGE_COUNT = 10**1000


@pytest.mark.parametrize(
    ("url", "lines"),
    [
        (
            "https://openai-compatible.example/v1",
            [
                'data: ' + json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
                'data: ' + json.dumps({
                    "choices": [],
                    "usage": {
                        "prompt_tokens": _ABSURD_USAGE_COUNT,
                        "completion_tokens": 1,
                    },
                }),
                "data: [DONE]",
            ],
        ),
        (
            "https://chatgpt.com/backend-api/codex/responses",
            [
                "event: response.output_text.delta",
                'data: ' + json.dumps({
                    "type": "response.output_text.delta",
                    "delta": "ok",
                }),
                "event: response.completed",
                'data: ' + json.dumps({
                    "type": "response.completed",
                    "response": {"usage": {
                        "input_tokens": _ABSURD_USAGE_COUNT,
                        "output_tokens": 1,
                    }},
                }),
            ],
        ),
        (
            "http://localhost:11434/api/chat",
            [json.dumps({
                "message": {"content": "ok"},
                "done": True,
                "prompt_eval_count": _ABSURD_USAGE_COUNT,
                "eval_count": 1,
            })],
        ),
        (
            "https://api.anthropic.com/v1/messages",
            [
                'data: ' + json.dumps({
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": _ABSURD_USAGE_COUNT}},
                }),
                'data: ' + json.dumps({
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "ok"},
                }),
                'data: ' + json.dumps({
                    "type": "message_delta",
                    "usage": {"output_tokens": 1},
                }),
                'data: ' + json.dumps({"type": "message_stop"}),
            ],
        ),
    ],
)
def test_provider_adapters_ignore_absurd_usage_without_losing_output(
    monkeypatch,
    url,
    lines,
):
    chunks = _run_provider_stream(monkeypatch, url, lines)

    assert any('"delta": "ok"' in chunk for chunk in chunks)
    assert "data: [DONE]\n\n" in chunks
    assert not any('"type": "usage"' in chunk for chunk in chunks)
    assert not any(chunk.startswith("event: error") for chunk in chunks)


@pytest.mark.parametrize(
    ("url", "reported_model", "lines"),
    [
        (
            "https://chatgpt.com/backend-api/codex/responses",
            "responses-provider-model",
            [
                "data: " + json.dumps({
                    "type": "response.created",
                    "response": {"model": "responses-provider-model"},
                }),
                "data: " + json.dumps({
                    "type": "response.output_text.delta",
                    "delta": "ok",
                }),
                "data: " + json.dumps({
                    "type": "response.completed",
                    "response": {
                        "model": "responses-provider-model",
                        "usage": {"input_tokens": 4, "output_tokens": 1},
                    },
                }),
            ],
        ),
        (
            "http://localhost:11434/api/chat",
            "ollama-provider-model",
            [json.dumps({
                "model": "ollama-provider-model",
                "message": {"content": "ok"},
                "done": True,
                "prompt_eval_count": 4,
                "eval_count": 1,
            })],
        ),
        (
            "https://api.anthropic.com/v1/messages",
            "anthropic-provider-model",
            [
                "data: " + json.dumps({
                    "type": "message_start",
                    "message": {
                        "model": "anthropic-provider-model",
                        "usage": {"input_tokens": 4},
                    },
                }),
                "data: " + json.dumps({
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "ok"},
                }),
                "data: " + json.dumps({
                    "type": "message_delta",
                    "usage": {"output_tokens": 1},
                }),
                "data: " + json.dumps({"type": "message_stop"}),
            ],
        ),
    ],
)
def test_native_stream_adapters_report_actual_model_and_usage(
    monkeypatch,
    url,
    reported_model,
    lines,
):
    chunks = _run_provider_stream(monkeypatch, url, lines)
    model_events = [
        json.loads(chunk[6:])
        for chunk in chunks
        if chunk.startswith("data: ") and '"type": "model_actual"' in chunk
    ]
    usage_events = [
        json.loads(chunk[6:])["data"]
        for chunk in chunks
        if chunk.startswith("data: ") and '"type": "usage"' in chunk
    ]

    assert model_events == [{
        "type": "model_actual",
        "requested_model": "configured-model",
        "model": reported_model,
    }]
    assert usage_events == [{
        "input_tokens": 4,
        "output_tokens": 1,
        "model": reported_model,
        "requested_model": "configured-model",
    }]
    assert any('"delta": "ok"' in chunk for chunk in chunks)
    assert "data: [DONE]\n\n" in chunks


def test_degenerate_stream_error_is_not_availability_evidence():
    guard = llm_core._DegenerateStreamGuard("looping-model")
    chunk = guard.check("repeat " * 100)

    assert chunk is not None
    assert json.loads(chunk.split("data: ", 1)[1])["fallback_eligible"] is False


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (httpx.WriteTimeout("write timed out"), 504),
        (httpx.RemoteProtocolError("peer disconnected"), 502),
    ],
)
def test_ambiguous_transport_failures_are_not_availability_evidence(monkeypatch, error, expected_status):
    class _RaisingClient:
        def stream(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _RaisingClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)

    async def run():
        return [
            chunk
            async for chunk in llm_core._stream_llm_inner(
                "https://openai-compatible.example/v1",
                "configured-model",
                [{"role": "user", "content": "hi"}],
            )
        ]

    chunks = asyncio.run(run())
    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload["status"] == expected_status
    assert payload["fallback_eligible"] is False


@pytest.mark.parametrize("error", [
    httpx.WriteTimeout("write timed out"),
    httpx.RemoteProtocolError("peer disconnected"),
])
def test_nonstream_foreground_does_not_advance_on_ambiguous_transport(monkeypatch, error):
    calls = []

    async def fake_call(url, model, messages, **kwargs):
        calls.append(model)
        raise error

    monkeypatch.setattr(llm_core, "llm_call_async", fake_call)

    with pytest.raises(type(error)):
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("https://selected.example/v1", "selected", {}),
                ("https://backup.example/v1", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={502, 504},
        ))

    assert calls == ["selected"]


@pytest.mark.parametrize("error", [
    httpx.WriteTimeout("write timed out"),
    httpx.RemoteProtocolError("peer disconnected"),
])
def test_nonstream_foreground_marks_adapter_transport_ineligible(monkeypatch, error):
    calls = []

    async def fake_post(client, url, headers, **kwargs):
        calls.append(url)
        raise error

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("https://selected.example/v1", "selected", {}),
                ("https://backup.example/v1", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={502, 504},
        ))

    assert len(calls) == 1
    assert getattr(exc.value, "fallback_eligible", None) is False


@pytest.mark.parametrize(
    ("error", "expected_models"),
    [
        (httpx.PoolTimeout("pool timed out"), ["selected", "backup"]),
        (httpx.WriteTimeout("write timed out"), ["selected"]),
    ],
)
def test_nonstream_foreground_advances_on_pool_timeout_but_not_write_timeout(
    monkeypatch,
    error,
    expected_models,
):
    calls = []

    async def fake_post(client, url, headers, **kwargs):
        model = kwargs["json"]["model"]
        calls.append(model)
        if model == "selected":
            raise error
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": "backup answer"}}]},
        )

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)

    if isinstance(error, httpx.PoolTimeout):
        response, route, actual_model = asyncio.run(
            llm_core.llm_call_async_with_route_fallback(
                [
                    ("https://selected.example/v1", "selected", {}),
                    ("https://backup.example/v1", "backup", {}),
                ],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={504},
            )
        )
        assert response == "backup answer"
        assert route[1] == "backup"
        assert actual_model == "backup"
    else:
        with pytest.raises(Exception) as exc:
            asyncio.run(llm_core.llm_call_async_with_route_fallback(
                [
                    ("https://selected.example/v1", "selected", {}),
                    ("https://backup.example/v1", "backup", {}),
                ],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={504},
            ))
        assert getattr(exc.value, "fallback_eligible", None) is False

    assert calls == expected_models


@pytest.mark.parametrize(
    ("error", "expected_models"),
    [
        (httpx.PoolTimeout("pool timed out"), ["selected", "backup"]),
        (httpx.WriteTimeout("write timed out"), ["selected"]),
    ],
)
def test_stream_foreground_advances_on_pool_timeout_but_not_write_timeout(
    monkeypatch,
    error,
    expected_models,
):
    calls = []

    class _RouteClient:
        def stream(self, method, url, **kwargs):
            model = kwargs["json"]["model"]
            calls.append(model)
            if model == "selected":
                class _FailureContext:
                    async def __aenter__(self):
                        raise error

                    async def __aexit__(self, *args):
                        return False

                return _FailureContext()
            return _ProviderStreamContext([
                'data: {"choices":[{"delta":{"content":"backup answer"}}]}',
                "data: [DONE]",
            ])

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _RouteClient())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [
                    ("https://selected.example/v1", "selected", {}),
                    ("https://backup.example/v1", "backup", {}),
                ],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={504},
                fallback_on_empty=False,
            )
        ]

    chunks = asyncio.run(run())

    assert calls == expected_models
    if isinstance(error, httpx.PoolTimeout):
        assert any('"delta": "backup answer"' in chunk for chunk in chunks)
        assert any('"type": "fallback"' in chunk for chunk in chunks)
    else:
        assert not any('"delta": "backup answer"' in chunk for chunk in chunks)
        assert chunks[0].startswith("event: error")


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        ({"type": "invalid_request_error", "message": "Unsupported model"}, 400),
        ({"type": "overloaded_error", "message": "Overloaded"}, 529),
        ({"type": "authentication_error", "message": "Invalid API key"}, 401),
    ],
)
def test_anthropic_stream_preserves_error_semantics(monkeypatch, error, expected_status):
    lines = ["data: " + json.dumps({"type": "error", "error": error})]

    chunks = _run_provider_stream(
        monkeypatch,
        "https://api.anthropic.com/v1/messages",
        lines,
    )

    assert len(chunks) == 1
    assert json.loads(chunks[0].split("data: ", 1)[1])["status"] == expected_status


def test_dedupe_candidates_keeps_first_of_each_route():
    """Exact route repeats are dropped while credential-distinct routes remain."""
    cands = [
        ("u1", "m1", {"h": 1}),   # first u1/m1 — kept
        ("u1", "m1", {"h": 2}),   # same provider/model, different credential — kept
        ("u2", "m2", {}),         # distinct — kept
        ("u1", "m1", {"h": 1}),   # exact repeat — dropped
        (None, "x", {}),          # malformed (no url) — dropped
        ("u3", "", {}),           # malformed (no model) — dropped
    ]
    assert llm_core.dedupe_model_candidates(cands) == [
        ("u1", "m1", {"h": 1}),
        ("u1", "m1", {"h": 2}),
        ("u2", "m2", {}),
    ]
    assert llm_core.dedupe_model_candidates([]) == []
    assert llm_core.dedupe_model_candidates(None) == []


def test_duplicate_route_is_attempted_only_once(monkeypatch):
    """A fallback that repeats the primary's (url, model) must NOT make the chain
    sail back into the same dead route — each distinct route is tried once."""
    calls = []

    async def fake_stream(url, model, messages, **kw):
        calls.append((url, model))
        yield 'event: error\ndata: {"status": 503, "text": "down"}\n\n'

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        out = []
        cands = [("u1", "m1", {}), ("u1", "m1", {}), ("u2", "m2", {})]
        async for c in llm_core.stream_llm_with_fallback(cands, [{"role": "user", "content": "hi"}]):
            out.append(c)
        return out

    asyncio.run(run())
    assert calls == [("u1", "m1"), ("u2", "m2")], f"duplicate route re-attempted: {calls}"


def test_same_provider_model_with_different_credentials_remains_ordered(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        key = kwargs["headers"]["Authorization"]
        calls.append(key)
        if key == "Bearer key-one":
            yield 'event: error\ndata: {"status": 429, "text": "rate limited"}\n\n'
        else:
            yield 'data: {"delta": "second account"}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [
                    ("https://provider.example/v1", "same-model", {"Authorization": "Bearer key-one"}),
                    ("https://provider.example/v1", "same-model", {"Authorization": "Bearer key-two"}),
                ],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={429},
                fallback_on_empty=False,
                candidate_route_descriptors=[
                    {"endpoint_id": "account-one", "endpoint_label": "Account one"},
                    {"endpoint_id": "account-two", "endpoint_label": "Account two"},
                ],
            )
        ]

    chunks = asyncio.run(run())

    assert calls == ["Bearer key-one", "Bearer key-two"]
    assert any('"delta": "second account"' in chunk for chunk in chunks)
    event = json.loads(next(chunk for chunk in chunks if '"type": "fallback"' in chunk)[6:])
    assert event["selected_endpoint_id"] == "account-one"
    assert event["answered_by_endpoint_id"] == "account-two"
    assert event["answered_by_endpoint_label"] == "Account two"


def test_invalid_primary_route_fails_closed_before_deduplication(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(model)
        yield 'data: {"delta": "must not run"}\n\n'

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("", "selected", {}), ("https://backup.example/v1", "backup", {})],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={503},
            )
        ]

    chunks = asyncio.run(run())
    assert calls == []
    assert len(chunks) == 1
    assert '"status": 400' in chunks[0]
    assert '"fallback_eligible": false' in chunks[0]


def test_nonstream_invalid_primary_route_fails_closed(monkeypatch):
    monkeypatch.setattr(
        llm_core,
        "llm_call_async",
        lambda *args, **kwargs: pytest.fail("invalid primary dispatched a request"),
    )

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [("", "selected", {}), ("https://backup.example/v1", "backup", {})],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={503},
        ))

    assert getattr(exc.value, "status_code", None) == 400
    assert getattr(exc.value, "fallback_eligible", None) is False


def test_subscription_collector_preserves_explicit_ineligible_marker(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(model)
        if model == "selected":
            yield 'event: error\ndata: {"status": 502, "error": "malformed frame", "fallback_eligible": false}\n\n'
        else:
            yield 'data: {"delta": "backup"}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("https://chatgpt.com/backend-api/codex/responses", "selected", {}),
                ("https://chatgpt.com/backend-api/codex/responses", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={502},
        ))

    assert calls == ["selected"]
    assert getattr(exc.value, "fallback_eligible", None) is False


def test_nonstream_request_configuration_error_is_ineligible(monkeypatch):
    calls = []

    async def fake_post(client, url, headers, **kwargs):
        calls.append(url)
        raise httpx.UnsupportedProtocol("unsupported protocol")

    monkeypatch.setattr(llm_core, "httpx_post_kimi_aware_async", fake_post)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_core, "_get_cached_response", lambda key: None)

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_core.llm_call_async_with_route_fallback(
            [
                ("ftp://selected.example", "selected", {}),
                ("https://backup.example/v1", "backup", {}),
            ],
            [{"role": "user", "content": "hi"}],
            fallback_statuses={502},
        ))

    assert len(calls) == 1
    assert getattr(exc.value, "fallback_eligible", None) is False


def test_multi_candidate_fallback_preserves_primary_reason_and_failure_chain(monkeypatch):
    async def fake_stream(url, model, messages, **kwargs):
        if model == "primary":
            yield 'event: error\ndata: {"status": 503, "text": "primary unavailable"}\n\n'
        elif model == "backup-a":
            yield 'event: error\ndata: {"status": 429, "text": "backup quota"}\n\n'
        else:
            yield 'data: {"delta": "answered"}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [
                    ("u1", "primary", {}),
                    ("u2", "backup-a", {}),
                    ("u3", "backup-b", {}),
                ],
                [{"role": "user", "content": "hi"}],
                fallback_statuses={429, 503},
                fallback_on_empty=False,
            )
        ]

    chunks = asyncio.run(run())
    event = json.loads(next(chunk for chunk in chunks if '"type": "fallback"' in chunk)[6:])

    assert event["selected_model"] == "primary"
    assert event["answered_by"] == "backup-b"
    assert "primary unavailable" in event["reason"]
    assert event["failures"] == [
        {
            "candidate_index": 0,
            "model": "primary",
            "status": 503,
        },
        {
            "candidate_index": 1,
            "model": "backup-a",
            "status": 429,
        },
    ]


def test_summarize_stream_error():
    assert "400" in llm_core._summarize_stream_error('event: error\ndata: {"status": 400, "text": "nope"}\n\n')
    assert llm_core._summarize_stream_error(None) == "primary model failed"
    assert llm_core._summarize_stream_error("garbage") == "primary model failed"
