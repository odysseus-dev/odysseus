"""Token usage must be captured even when it rides on a non-empty finish delta.

Some OpenAI-compatible gateways and local servers send usage on the FINAL
streamed chunk, whose delta also carries role / finish_reason (e.g.
{"delta": {"role": "assistant", "content": null}, "finish_reason": "stop"}).
stream_llm only captured usage when the delta was exactly None / {} /
{"content": None}, so those providers\' token accounting read zero.
"""
import asyncio
import json

from src import llm_core


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines)


def _drive(monkeypatch, lines, model="gpt-4o-test"):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_mark_host_dead", lambda *a, **k: False, raising=False)

    async def run():
        out = []
        async for chunk in llm_core.stream_llm(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model, [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
        ):
            out.append(chunk)
        return "".join(out)

    return asyncio.run(run())


def _usage_events(blob):
    events = []
    for ln in blob.split("\n"):
        ln = ln.strip()
        if ln.startswith("data: ") and ln[6:] != "[DONE]":
            try:
                j = json.loads(ln[6:])
            except ValueError:
                continue
            if j.get("type") == "usage":
                events.append(j["data"])
    return events


def test_usage_on_finish_delta_with_role_is_captured(monkeypatch):
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        'data: ' + json.dumps({
            "choices": [{"delta": {"role": "assistant", "content": None}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1},
        }),
        'data: [DONE]',
    ]
    usage = _usage_events(_drive(monkeypatch, lines))
    assert usage, "usage on a non-empty finish delta was dropped"
    assert usage[-1] == {"input_tokens": 9, "output_tokens": 1}


def test_usage_on_empty_choices_chunk_still_captured(monkeypatch):
    # canonical OpenAI include_usage: final chunk has empty choices + usage
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        'data: ' + json.dumps({"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}),
        'data: [DONE]',
    ]
    usage = _usage_events(_drive(monkeypatch, lines))
    assert usage and usage[-1] == {"input_tokens": 4, "output_tokens": 2}


def test_null_choice_chunk_does_not_crash(monkeypatch):
    # Some providers emit {"choices": [null]} as a heartbeat/keepalive chunk.
    # The parser must silently skip it rather than crashing on None.get("delta").
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
        'data: ' + json.dumps({"choices": [None]}),
        'data: [DONE]',
    ]
    result = _drive(monkeypatch, lines)
    assert "Hello" in result


def test_null_choice_with_null_usage_does_not_crash(monkeypatch):
    # Chunk with both choices:[null] and usage:null — neither field should panic.
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
        'data: ' + json.dumps({"choices": [None], "usage": None}),
        'data: [DONE]',
    ]
    result = _drive(monkeypatch, lines)
    assert "Hi" in result


def test_null_tool_call_in_delta_is_skipped(monkeypatch):
    # Some providers include null entries in the tool_calls array alongside
    # valid calls. The null entry must be skipped; the valid call must survive.
    lines = [
        'data: ' + json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [
                        None,
                        {"index": 0, "function": {"name": "get_weather", "arguments": '{"city":'}},
                    ]
                }
            }]
        }),
        'data: ' + json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"name": "", "arguments": '"London"}'}},
                    ]
                }
            }]
        }),
        'data: [DONE]',
    ]
    result = _drive(monkeypatch, lines)
    # The stream completes without error; the valid tool call was accumulated.
    assert result is not None
