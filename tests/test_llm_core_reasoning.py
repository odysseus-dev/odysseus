"""Regression: a streamed `reasoning` delta (vLLM 0.20.2 / NIM / Ollama) must surface
as a thinking chunk, while a `content` delta still streams as normal content. Also
covers the older `reasoning_content` field name for backward compatibility.
"""
import asyncio
import json

from src import llm_core


class _FakeResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):  # only used on non-200; present for safety
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *args, **kwargs):
        return _FakeStreamCtx(self._lines)


def _run_stream(model, lines, monkeypatch):
    """Drive stream_llm against a faked upstream and return parsed SSE payloads."""
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))

    async def _go():
        out = []
        async for chunk in llm_core.stream_llm(
            "http://nim-nano:8000/v1/chat/completions",
            model,
            [{"role": "user", "content": "hi"}],
        ):
            out.append(chunk)
        return out

    parsed = []
    for chunk in asyncio.run(_go()):
        for raw in chunk.splitlines():
            raw = raw.strip()
            if raw.startswith("data:"):
                payload = raw[5:].strip()
                if payload.startswith("{"):
                    try:
                        parsed.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
    return [p for p in parsed if "delta" in p]


def test_reasoning_field_emits_thinking_chunk(monkeypatch):
    deltas = _run_stream(
        "nvidia/nemotron-3-nano",
        [
            'data: {"choices":[{"delta":{"reasoning":"weighing options"}}]}',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    assert any(d.get("thinking") and "weighing options" in d["delta"] for d in deltas), deltas
    assert any((not d.get("thinking")) and d["delta"] == "Hello" for d in deltas), deltas


def test_reasoning_content_field_still_supported(monkeypatch):
    # Older builds emit `reasoning_content`; it must still surface as thinking.
    deltas = _run_stream(
        "some-thinking-model",
        [
            'data: {"choices":[{"delta":{"reasoning_content":"older field"}}]}',
            'data: {"choices":[{"delta":{"content":"Answer"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    assert any(d.get("thinking") and "older field" in d["delta"] for d in deltas), deltas
    assert any((not d.get("thinking")) and d["delta"] == "Answer" for d in deltas), deltas


def test_think_tag_in_content_stream_routes_to_thinking_channel(monkeypatch):
    # Regression: unregistered model (Qwopus-style) that emits <think>…</think>
    # directly in the content field. Reasoning must surface as thinking chunks;
    # only the answer after </think> is a normal delta.
    deltas = _run_stream(
        "Qwopus3-9B-custom",  # name not in _THINKING_MODEL_PATTERNS
        [
            'data: {"choices":[{"delta":{"content":"<think>step one "}}]}',
            'data: {"choices":[{"delta":{"content":"step two"}}]}',
            'data: {"choices":[{"delta":{"content":"</think>Final answer"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    thinking = [d for d in deltas if d.get("thinking")]
    regular = [d for d in deltas if not d.get("thinking")]
    assert thinking, f"expected thinking deltas, got: {deltas}"
    assert all("Final answer" not in d["delta"] for d in thinking), thinking
    assert regular, f"expected regular delta after </think>, got: {deltas}"
    assert any("Final answer" in d["delta"] for d in regular), regular


def test_think_tag_and_close_in_same_chunk(monkeypatch):
    # <think>reasoning</think>answer all arrive in a single content chunk.
    deltas = _run_stream(
        "Qwopus3-9B-custom",
        [
            'data: {"choices":[{"delta":{"content":"<think>my reasoning</think>my answer"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    thinking = [d for d in deltas if d.get("thinking")]
    regular = [d for d in deltas if not d.get("thinking")]
    assert thinking and "my reasoning" in thinking[0]["delta"], thinking
    assert regular and "my answer" in regular[0]["delta"], regular


def test_think_tag_gt_in_mid_reasoning_not_truncated(monkeypatch):
    # Regression for _first_content_sent misuse: the opening-tag strip ran on every
    # chunk (not just the first) because _first_content_sent stays False throughout
    # the think block. On chunk 2 it did find(">") over reasoning text and silently
    # dropped everything before the first ">". Repro: 3 chunks, ">" in chunk 2.
    deltas = _run_stream(
        "Qwopus3-9B-custom",
        [
            'data: {"choices":[{"delta":{"content":"<think>reasoning a "}}]}',
            'data: {"choices":[{"delta":{"content":"more c > d "}}]}',
            'data: {"choices":[{"delta":{"content":"</think>answer"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    thinking = [d for d in deltas if d.get("thinking")]
    regular = [d for d in deltas if not d.get("thinking")]
    # "more c " must survive — must not be truncated at the '>'
    assert any("more c > d" in d["delta"] for d in thinking), thinking
    assert any("answer" in d["delta"] for d in regular), regular


def test_registered_thinking_model_stray_close_tag_repair_unchanged(monkeypatch):
    # The existing </think> repair for registered models must not regress.
    # A registered model that starts content with </think> gets <think> prepended.
    deltas = _run_stream(
        "qwq-32b",  # registered in _THINKING_MODEL_PATTERNS
        [
            'data: {"choices":[{"delta":{"content":"</think>Here is my answer"}}]}',
            "data: [DONE]",
        ],
        monkeypatch,
    )
    assert deltas, deltas
    first = deltas[0]["delta"]
    assert first.startswith("<think>"), f"expected repair prefix, got: {first!r}"
