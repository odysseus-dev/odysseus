"""Regression: the tool-execution task inside stream_agent_loop must be
cancelled (not orphaned) when the SSE consumer stops draining the generator
early — e.g. a client disconnect mid tool-call.

The drain loop in stream_agent_loop:

    _tool_task = asyncio.create_task(_run_tool())
    while True:
        evt = await _progress_q.get()
        if evt is None:
            break
        yield ...
    desc, result = await _tool_task

used to have no try/finally around it. If the generator is closed while
suspended on `await _progress_q.get()` (which is exactly what Starlette does
via `aclose()` when an SSE client disconnects), GeneratorExit is thrown at
that point and `_tool_task` is abandoned mid-flight — never awaited, never
cancelled. For a long-running `bash`/`python` tool this orphans the
subprocess server-side with nothing left to reap it.

The fix wraps the drain loop in try/finally and cancels+awaits `_tool_task`
on early exit. This test drives the real stream_agent_loop with a fake tool
handler that sleeps until cancelled, closes the generator mid-tool-call (the
same way a dropped SSE connection would), and asserts the fake handler
actually observed cancellation.
"""
import asyncio
import json

import src.agent_loop as al


def test_tool_task_cancelled_on_generator_close(monkeypatch):
    cancelled = {"v": False}

    async def _slow_exec(block, *a, progress_cb=None, **k):
        if progress_cb:
            await progress_cb({"elapsed_s": 1, "tail": "running"})
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled["v"] = True
            raise
        return ("bash", {"output": "ok", "exit_code": 0})

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _slow_exec, raising=False)

    native_calls = [{"name": "bash", "arguments": json.dumps({"command": "sleep 60"})}]

    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": "Running it now."})}\n\n'
        yield f'data: {json.dumps({"type": "tool_calls", "calls": native_calls})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    async def _run():
        gen = al.stream_agent_loop(
            "https://api.openai.com/v1", "gpt-4o",
            [{"role": "user", "content": "run sleep 60"}],
            max_rounds=2,
            relevant_tools={"bash"},
        )
        saw_tool_start = False
        saw_tool_progress = False
        async for chunk in gen:
            if '"type": "tool_start"' in chunk:
                saw_tool_start = True
            elif '"type": "tool_progress" ' in chunk or '"type": "tool_progress"' in chunk:
                saw_tool_progress = True
                break
        assert saw_tool_start, "expected a tool_start event before the tool ran"
        assert saw_tool_progress, "expected a tool_progress event once the fake tool started (task must exist by now)"
        # Simulate an SSE client disconnecting mid tool-call: close the
        # generator while it is suspended awaiting the next progress event.
        await gen.aclose()
        # Assert *inside* this coroutine, immediately after aclose() returns.
        # asyncio.run()'s own shutdown sequence cancels any tasks still
        # pending once _run() itself completes — checking after asyncio.run()
        # returns would pass even with the bug, because that unrelated
        # cleanup would cancel the orphaned task anyway and mask the fix.
        assert cancelled["v"] is True, (
            "tool task must be cancelled by stream_agent_loop's own cleanup "
            "on generator close, not left running until asyncio.run() tears "
            "down the loop"
        )

    asyncio.run(_run())
