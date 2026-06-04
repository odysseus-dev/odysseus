"""Backend time-to-first-token guard for agent mode (issue #280).

A reachable endpoint that accepts the request but never streams a token —
small-context overflow or no tool-calling, where some backends stall silently —
would otherwise hang until the full agent stream timeout. `_first_token_guard`
bounds the wait for the first chunk: on timeout it emits one actionable
`event: error` and stops; once chunks flow it is transparent.
"""
import asyncio
import json

import pytest

from src.agent_loop import _first_token_guard


async def _never_yields():
    await asyncio.sleep(30)
    yield "data: never\n\n"  # unreachable within the test


async def _yields(items):
    for it in items:
        yield it


async def test_emits_actionable_error_when_no_first_token():
    out = [chunk async for chunk in _first_token_guard(_never_yields(), 0.2)]
    assert len(out) == 1
    assert out[0].startswith("event: error")
    payload = json.loads(out[0].split("data: ", 1)[1])
    assert payload["status"] == 504
    # The message the SSE client renders lives in `text`; must name the real fix.
    assert "context" in payload["text"].lower()
    assert "tool calling" in payload["text"].lower()


async def test_transparent_once_streaming_starts():
    out = [chunk async for chunk in _first_token_guard(_yields(["a", "b", "c"]), 0.2)]
    assert out == ["a", "b", "c"]


async def test_slow_first_token_under_budget_is_not_cancelled():
    async def _slow():
        await asyncio.sleep(0.05)
        yield "first"
        yield "second"

    out = [chunk async for chunk in _first_token_guard(_slow(), 0.5)]
    assert out == ["first", "second"]
