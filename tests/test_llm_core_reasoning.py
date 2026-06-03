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
