"""Error-path tests for LLM streaming in src/llm_core.py.

Verifies that when a streaming LLM connection fails mid-stream (ReadTimeout,
ConnectError, NetworkError), the generator yields a properly-formed SSE error
event instead of raising or silently stopping.

These tests use async generators and monkeypatching — no running server needed.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-llm-stream-test-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def _collect(gen) -> list[str]:
    """Drain an async generator into a list of strings."""
    result = []
    async for chunk in gen:
        result.append(chunk)
    return result


def _parse_sse_event(chunks: list[str]) -> dict | None:
    """Find the first SSE error event in a chunk list and parse its data."""
    for chunk in chunks:
        if chunk.startswith("event: error\n"):
            data_line = chunk.split("\n")[1]
            if data_line.startswith("data: "):
                return json.loads(data_line[6:])
    return None


def _make_streaming_response(lines: list[str], *, raise_after: Exception | None = None):
    """Build a mock httpx streaming response that yields lines then optionally raises."""
    async def _aiter_lines():
        for line in lines:
            yield line
        if raise_after is not None:
            raise raise_after

    resp = MagicMock()
    resp.status_code = 200
    resp.aiter_lines = _aiter_lines
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


# ── Ollama stream errors ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_stream_read_timeout_yields_error_event(monkeypatch):
    """ReadTimeout mid-stream → SSE error event with status 504."""
    import src.llm_core as llm

    resp = _make_streaming_response(
        lines=['{"model":"llama3","message":{"content":"Hi"},"done":false}'],
        raise_after=httpx.ReadTimeout("timed out"),
    )

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_get_http_client", lambda: mock_client)

    gen = llm.stream_llm(
        url="http://localhost:11434/api/chat",
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    chunks = await _collect(gen)

    error = _parse_sse_event(chunks)
    assert error is not None, f"Expected SSE error event, got: {chunks}"
    assert error["status"] == 504
    assert "timeout" in error["error"].lower()


@pytest.mark.asyncio
async def test_ollama_stream_connect_error_yields_error_event(monkeypatch):
    """ConnectError → SSE error event with status 503."""
    import src.llm_core as llm

    resp = _make_streaming_response(lines=[], raise_after=httpx.ConnectError("refused"))

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_get_http_client", lambda: mock_client)

    gen = llm.stream_llm(
        url="http://localhost:11434/api/chat",
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    chunks = await _collect(gen)

    error = _parse_sse_event(chunks)
    assert error is not None, f"Expected SSE error event, got: {chunks}"
    assert error["status"] == 503


@pytest.mark.asyncio
async def test_ollama_stream_network_error_yields_error_event(monkeypatch):
    """NetworkError mid-stream → SSE error event with status 502."""
    import src.llm_core as llm

    resp = _make_streaming_response(
        lines=['{"model":"llama3","message":{"content":"Par"},"done":false}'],
        raise_after=httpx.NetworkError("network failure"),
    )

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_get_http_client", lambda: mock_client)

    gen = llm.stream_llm(
        url="http://localhost:11434/api/chat",
        model="llama3",
        messages=[{"role": "user", "content": "hi"}],
    )
    chunks = await _collect(gen)

    error = _parse_sse_event(chunks)
    assert error is not None, f"Expected SSE error event, got: {chunks}"
    assert error["status"] == 502


# ── OpenAI-compatible stream errors ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_stream_read_timeout_yields_error_event(monkeypatch):
    """ReadTimeout on OpenAI-compatible endpoint → SSE error event with status 504."""
    import src.llm_core as llm

    resp = _make_streaming_response(
        lines=['data: {"choices":[{"delta":{"content":"Hello"}}]}'],
        raise_after=httpx.ReadTimeout("timed out"),
    )

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_get_http_client", lambda: mock_client)

    gen = llm.stream_llm(
        url="http://localhost:1234/v1/chat/completions",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    chunks = await _collect(gen)

    error = _parse_sse_event(chunks)
    assert error is not None, f"Expected SSE error event, got: {chunks}"
    assert error["status"] == 504


@pytest.mark.asyncio
async def test_openai_stream_connect_error_yields_error_event(monkeypatch):
    """ConnectError on OpenAI-compatible endpoint → SSE error event with status 503."""
    import src.llm_core as llm

    resp = _make_streaming_response(lines=[], raise_after=httpx.ConnectError("refused"))

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=resp)
    monkeypatch.setattr(llm, "_get_http_client", lambda: mock_client)

    gen = llm.stream_llm(
        url="http://localhost:1234/v1/chat/completions",
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    chunks = await _collect(gen)

    error = _parse_sse_event(chunks)
    assert error is not None, f"Expected SSE error event, got: {chunks}"
    assert error["status"] == 503
