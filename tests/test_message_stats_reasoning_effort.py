"""Message stats show the request's actual reasoning effort and temperature.

The effort is read back from the request payload that was sent, travels on the
stream's usage event into the per-message metrics, and is rendered only when
present.
"""
import asyncio
import json
from pathlib import Path

import pytest

from src import llm_core
from src.agent_loop import _compute_final_metrics

ROOT = Path(__file__).resolve().parents[1]
RENDERER = (ROOT / "static/js/chatRenderer.js").read_text()

_CHATGPT_URL = "https://chatgpt.com/backend-api/codex/responses"
_LOCAL_URL = "http://127.0.0.1:8081/v1/chat/completions"
_MESSAGES = [{"role": "user", "content": "hi"}]


class _Resp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _Ctx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _Resp(self._lines)

    async def __aexit__(self, *args):
        return False


class _Client:
    def __init__(self, lines):
        self._lines = lines
        self.payloads = []

    def stream(self, method, url, **kwargs):
        self.payloads.append(kwargs.get("json"))
        return _Ctx(self._lines)


def _usage(monkeypatch, url, model, lines, effort, **kwargs):
    client = _Client(lines)
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "get_context_length", lambda *a, **k: 4096)

    async def run():
        usage = None
        async for chunk in llm_core._stream_llm_inner(
            url, model, _MESSAGES, headers={"Authorization": "Bearer t"}, reasoning_effort=effort,
            **kwargs,
        ):
            for line in chunk.split("\n"):
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    try:
                        event = json.loads(line[6:])
                    except ValueError:
                        continue
                    if event.get("type") == "usage":
                        usage = event["data"]
        return usage

    return client, asyncio.run(run())


_CHATGPT_STREAM = [
    "data: " + json.dumps({"type": "response.output_text.delta", "delta": "hi"}),
    "data: " + json.dumps({"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 2}}}),
]
_LOCAL_STREAM = [
    "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": "hi"}}]}),
    "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}),
    "data: [DONE]",
]


def test_chatgpt_usage_reports_the_effort_the_request_carried(monkeypatch):
    client, usage = _usage(monkeypatch, _CHATGPT_URL, "gpt-5.5", _CHATGPT_STREAM, "low")
    assert client.payloads[0]["reasoning"] == {"effort": "low"}
    assert usage["reasoning_effort"] == "low"


def test_usage_omits_effort_when_none_was_picked(monkeypatch):
    client, usage = _usage(monkeypatch, _CHATGPT_URL, "gpt-5.5", _CHATGPT_STREAM, None)
    assert "reasoning" not in client.payloads[0]
    assert "reasoning_effort" not in usage


def test_usage_omits_effort_the_transport_did_not_send(monkeypatch):
    # A picked effort the payload does not carry must not be reported.
    client, usage = _usage(monkeypatch, _LOCAL_URL, "qwen-local", _LOCAL_STREAM, "low")
    assert "reasoning_effort" not in client.payloads[0]
    assert "reasoning_effort" not in usage


def test_annotation_reads_both_payload_shapes_and_ignores_other_values():
    annotate = llm_core._annotate_usage_effort
    assert annotate({}, {"reasoning": {"effort": "high"}}, "High") == {"reasoning_effort": "high"}
    assert annotate({}, {"reasoning_effort": "low"}, "low") == {"reasoning_effort": "low"}
    # A different value in the payload (e.g. a provider constant) is not the pick.
    assert annotate({}, {"reasoning_effort": "high"}, "low") == {}
    assert annotate({}, {"reasoning_effort": "low"}, None) == {}


def _metrics(**overrides):
    kwargs = dict(
        messages=_MESSAGES, full_response="hello", total_duration=1.0, time_to_first_token=0.1,
        context_length=4096, real_input_tokens=3, real_output_tokens=2, has_real_usage=True,
        tool_events=[], round_texts=[], model="m",
    )
    kwargs.update(overrides)
    return _compute_final_metrics(**kwargs)


def test_final_metrics_carry_effort_only_when_applied():
    assert _metrics(reasoning_effort="low")["reasoning_effort"] == "low"
    assert "reasoning_effort" not in _metrics()


def test_stats_panel_renders_the_effort_row_only_when_recorded():
    assert "metrics.reasoning_effort" in RENDERER
    assert "${reasoningEffort ? `<div class=\"ctx-stat-row\"><span class=\"ctx-label\">Reasoning effort</span>" in RENDERER


@pytest.mark.parametrize("payload,expected", [
    ({"temperature": 0}, 0),
    ({"temperature": 0.65}, 0.65),
    ({"options": {"temperature": 0}}, 0),
    ({"options": {"temperature": 0.4}}, 0.4),
    ({}, None),
    ({"temperature": None}, None),
    ({"temperature": True}, None),
    ({"temperature": "0.7"}, None),
    ({"temperature": float("nan")}, None),
    ({"temperature": float("inf")}, None),
])
def test_temperature_annotation_uses_only_finite_sent_values(payload, expected):
    usage = llm_core._annotate_usage_temperature({"input_tokens": 3}, payload)
    assert usage["input_tokens"] == 3
    if expected is None:
        assert "temperature" not in usage
    else:
        assert usage["temperature"] == expected


_ANTHROPIC_STREAM = [
    "data: " + json.dumps({"type": "message_start", "message": {"usage": {"input_tokens": 3}}}),
    "data: " + json.dumps({"type": "message_delta", "usage": {"output_tokens": 2}}),
    "data: " + json.dumps({"type": "message_stop"}),
]
_OLLAMA_STREAM = [
    json.dumps({"message": {"content": "hi"}, "done": False}),
    json.dumps({"message": {}, "done": True, "prompt_eval_count": 3, "eval_count": 2}),
]


@pytest.mark.parametrize("url,model,lines,requested,expected", [
    (_LOCAL_URL, "simple-model", _LOCAL_STREAM, 0, 0),
    (_LOCAL_URL, "simple-model", _LOCAL_STREAM, 0.35, 0.35),
    (_LOCAL_URL, "gpt-5.5", _LOCAL_STREAM, 0.35, None),
    (_CHATGPT_URL, "gpt-5.5", _CHATGPT_STREAM, 0.35, None),
    (_CHATGPT_URL, "gpt-4.1", _CHATGPT_STREAM, 0.35, 0.35),
    ("https://api.anthropic.com/v1/messages", "claude-sonnet-4-6", _ANTHROPIC_STREAM, 1.7, 1),
    ("https://api.anthropic.com/v1/messages", "claude-opus-4-7", _ANTHROPIC_STREAM, 0.35, None),
    ("http://127.0.0.1:11434/api/chat", "simple-model", _OLLAMA_STREAM, 0, 0),
    ("http://127.0.0.1:11434/api/chat", "simple-model", _OLLAMA_STREAM, 0.4, 0.4),
])
def test_stream_usage_reports_final_payload_temperature(monkeypatch, url, model, lines, requested, expected):
    client, usage = _usage(monkeypatch, url, model, lines, None, temperature=requested)
    payload = client.payloads[0]
    sent = payload.get("temperature", (payload.get("options") or {}).get("temperature"))
    assert sent == expected
    if expected is None:
        assert "temperature" not in usage
    else:
        assert usage["temperature"] == expected


def test_temperature_reports_provider_adjustment_not_requested_value(monkeypatch):
    def force_temperature(payload, *args):
        payload["temperature"] = 0

    monkeypatch.setattr(llm_core, "_apply_local_generation_stability", force_temperature)
    client, usage = _usage(monkeypatch, _LOCAL_URL, "simple-model", _LOCAL_STREAM, None, temperature=0.8)
    assert client.payloads[0]["temperature"] == usage["temperature"] == 0


@pytest.mark.parametrize("temperature", [0, 0.7])
def test_final_metrics_preserve_message_temperature(temperature):
    first = _metrics(temperature=temperature)
    _metrics(temperature=1.5)
    assert first["temperature"] == temperature
    assert "temperature" not in _metrics()


def test_stats_panel_renders_temperature_including_zero_only_when_recorded():
    assert "Number.isFinite(metrics.temperature) ? metrics.temperature : null" in RENDERER
    assert "${temperature !== null ? `<div class=\"ctx-stat-row\"><span class=\"ctx-label\">Temperature</span>" in RENDERER
