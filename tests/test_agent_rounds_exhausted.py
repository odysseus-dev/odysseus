"""Regression: stream_agent_loop emits `rounds_exhausted` only when the round
cap is hit while still working, and NOT on a normal finish.

The decision is a `for/else` in the loop: the `else` runs only if no `break`
fired (break = done / budget / error). A refactor that adds a stray break or
return, or moves the done-break, could silently flip this. See PR #1999 / #1997.
"""

import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _types(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch):
    # Skip RAG/tool-index, MCP, and settings lookups; keep the real loop body,
    # _resolve_tool_blocks, and parse_tool_blocks.
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    # The loop-breaker intentionally performs one final tool-free synthesis.
    # Keep this unit test offline and deterministic instead of letting that
    # fallback contact the configured model endpoint.
    async def _fake_synthesis(*args, **kwargs):
        return "Stopped after detecting a repeated command."
    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_synthesis)


def _run_loop(monkeypatch, round_text, max_rounds=2):
    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": round_text})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "do a long multi-step task"}],
        max_rounds=max_rounds,
        relevant_tools={"bash"},
        _is_teacher_run=True,
    )
    return _types(_collect(gen))


def test_emits_rounds_exhausted_when_cap_hit_mid_task(monkeypatch):
    _patch_common(monkeypatch)
    # Every round returns a tool block -> never "done" -> loop exhausts the cap.
    events = _run_loop(monkeypatch, "```bash\necho hi\n```", max_rounds=2)
    assert any(e.get("type") == "rounds_exhausted" for e in events), events


def test_no_rounds_exhausted_on_normal_finish(monkeypatch):
    _patch_common(monkeypatch)
    # A plain answer (no tool block) -> done-break on round 1 -> no event.
    events = _run_loop(monkeypatch, "All done, here is your answer.", max_rounds=2)
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events


def test_emits_intent_nudge_exhausted_when_cap_is_exhausted(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(monkeypatch, "Let me check the logs", max_rounds=5)

    guard = next((e for e in events if e.get("type") == "intent_nudge_exhausted"), None)
    assert guard is not None, events
    assert guard["reason"] == "intent_without_action_nudge_cap"
    assert guard["nudges"] == 2


def test_emits_loop_breaker_triggered_when_loop_breaker_trips(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(monkeypatch, "```bash\necho hi\n```", max_rounds=6)

    guard = next((e for e in events if e.get("type") == "loop_breaker_triggered"), None)
    assert guard is not None, events
    assert guard["reason"] == "loop_breaker_stall"


def test_bash_output_replay_is_blocked_before_execution(monkeypatch):
    _patch_common(monkeypatch)
    listing = (
        "total 8\n"
        "drwxr-xr-x 2 user user 4.0K Aug 13 12:00 .\n"
        "-rw-r--r-- 1 user user 120 Aug 13 11:00 notes.txt"
    )
    replies = iter([
        "```bash\nls -lhSra\n```",
        f"```bash\n{listing}\n```",
        "The listing is shown above.",
    ])

    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": next(replies)})}\n\n'
        yield "data: [DONE]\n\n"

    calls = []

    async def _fake_exec(block, *args, **kwargs):
        calls.append(block.content)
        return "bash", {"output": listing, "exit_code": 0}

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1",
        "m",
        [{"role": "user", "content": "list the workspace"}],
        max_rounds=3,
        relevant_tools={"bash"},
        _is_teacher_run=True,
    )))

    assert calls == ["ls -lhSra"]
    blocked = [
        event for event in events
        if event.get("type") == "tool_output" and event.get("exit_code") == 126
    ]
    assert blocked
    assert "copied from the preceding command output" in blocked[0]["output"]
