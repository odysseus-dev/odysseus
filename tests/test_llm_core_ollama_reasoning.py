import asyncio
import json
from unittest.mock import patch

import pytest

from src import llm_core


class _DummyStreamResponse:
    def __init__(self):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        chunk = {
            "choices": [
                {
                    "delta": {"content": "Final answer after tool call."},
                    "finish_reason": "stop",
                }
            ]
        }
        yield f"data: {json.dumps(chunk)}"
        yield "data: [DONE]"


@pytest.mark.parametrize(
    "url,model,expected_effort",
    [
        ("http://127.0.0.1:11434/v1/chat/completions", "gemma4:31b", "none"),
        ("http://localhost:11434/v1/chat/completions", "qwen3:32b", "none"),
        ("http://127.0.0.1:1234/v1/chat/completions", "gemma4:31b", None),
        ("http://127.0.0.1:8000/v1/chat/completions", "qwen3:32b", None),
    ],
)
def test_stream_llm_sets_reasoning_effort_none_only_for_ollama_compat(
    url: str, model: str, expected_effort
):
    captured_payloads = []

    class _DummyClient:
        def stream(self, method, target_url, json=None, headers=None, timeout=None):
            captured_payloads.append(dict(json or {}))
            return _DummyStreamResponse()

    async def runner():
        with patch.object(llm_core, "_get_http_client", return_value=_DummyClient()):
            chunks = []
            async for item in llm_core._stream_llm_inner(
                url,
                model,
                [{"role": "user", "content": "Hello"}],
                _retry_silent_local=False,
            ):
                chunks.append(item)
            return chunks

    asyncio.run(runner())
    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("reasoning_effort") == expected_effort


def test_stream_llm_preserves_explicit_reasoning_effort_on_ollama_compat():
    captured_payloads = []

    class _DummyClient:
        def stream(self, method, target_url, json=None, headers=None, timeout=None):
            captured_payloads.append(dict(json or {}))
            return _DummyStreamResponse()

    async def runner():
        with patch.object(llm_core, "_get_http_client", return_value=_DummyClient()):
            async for _ in llm_core._stream_llm_inner(
                "http://127.0.0.1:11434/v1/chat/completions",
                "gemma4:31b",
                [{"role": "user", "content": "Hello"}],
                reasoning_effort="low",
                _retry_silent_local=False,
            ):
                pass

    asyncio.run(runner())
    assert len(captured_payloads) == 1
    assert captured_payloads[0].get("reasoning_effort") == "low"
