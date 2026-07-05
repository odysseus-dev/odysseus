"""Issue #4589 — _resolve_model does a blocking httpx.get, so calling it
directly from an async handler stalls the whole event loop for the duration of
the probe. The async call sites now wrap it in asyncio.to_thread.

chat_with_model is used as the representative handler: _resolve_model is the
first real work it does, and a ValueError returns early before any LLM call, so
these tests drive the offload path without a live model endpoint.

(Previously used do_pipeline, which was refactored into PipelineTool as part
of the tool -> agent_tools migration #3629.)
"""

import asyncio
import threading
import time

import src.ai_interaction as ai
from src.agent_tools.model_interaction_tools import chat_with_model


async def test_chat_with_model_resolves_model_off_the_event_loop(monkeypatch):
    # A deliberately blocking _resolve_model that records how many copies run
    # at once. If it ran on the event loop, the first call would block the loop
    # and the second could not start — peak concurrency would be 1.
    state = {"active": 0, "peak": 0}
    lock = threading.Lock()

    def slow_resolve(spec, owner=None):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.2)
        with lock:
            state["active"] -= 1
        raise ValueError("no such model")  # early-return path, no LLM call

    monkeypatch.setattr(ai, "_resolve_model", slow_resolve)

    content = "m\nmessage body"  # first line = model spec, rest = message
    results = await asyncio.gather(
        chat_with_model(content, owner="u"),
        chat_with_model(content, owner="u"),
    )

    assert all("error" in r for r in results)
    assert state["peak"] == 2, "resolutions did not overlap — call still blocks the loop"


async def test_chat_with_model_uses_offloaded_resolution_result(monkeypatch):
    # The offload must also return the resolved tuple, not just propagate errors.
    monkeypatch.setattr(
        ai, "_resolve_model",
        lambda spec, owner=None: ("http://x/v1/chat/completions", "resolved-model", {}),
    )

    async def fake_llm(url, model, messages, **kwargs):
        return f"output from {model}"

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm)

    result = await chat_with_model("m\nhello", owner="u")

    assert "error" not in result, result
    # The model the offloaded _resolve_model returned made it through to the call.
    assert "resolved-model" in str(result)
