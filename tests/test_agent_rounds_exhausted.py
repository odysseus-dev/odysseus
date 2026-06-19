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


def test_emits_explicit_process_and_final_round_events(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        text = "I will inspect.\n```bash\necho hi\n```" if calls == 1 else "Done."
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "inspect then answer"}],
        max_rounds=2,
        max_tool_calls=7,
        relevant_tools={"bash"},
        workspace="/tmp/workspace",
    )
    events = _types(_collect(gen))

    process_events = [e for e in events if e.get("type") == "agent_process"]
    final_events = [e for e in events if e.get("type") == "agent_final"]
    assert process_events and process_events[0]["round"] == 1
    assert process_events[0]["text"] == "I will inspect."
    assert final_events and final_events[-1]["round"] == 2
    assert final_events[-1]["text"] == "Done."
    metrics = [e["data"] for e in events if e.get("type") == "metrics"][-1]
    assert metrics["agent_limits"]["max_rounds"] == 2
    assert metrics["agent_limits"]["max_tool_calls"] == 7
    assert metrics["agent_limits"]["rounds_used"] == 2
    assert metrics["agent_limits"]["tool_calls_used"] == 1
    assert metrics["agent_limits"]["workspace_bound"] is True


def test_incomplete_no_tool_promise_gets_recovery_round(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0
    message_snapshots = []

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        message_snapshots.append([dict(m) for m in messages])
        text = (
            "I'll inspect the workspace files and summarize what I find."
            if calls == 1
            else "Done: README.txt is present."
        )
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect the workspace files and summarize them."}],
        max_rounds=3,
        relevant_tools={"bash", "ls"},
    )
    events = _types(_collect(gen))

    assert calls == 2
    assert any(e.get("type") == "agent_step" and e.get("round") == 2 for e in events), events
    assert any(
        e.get("type") == "agent_process"
        and e.get("text") == "I'll inspect the workspace files and summarize what I find."
        for e in events
    )
    assert not any(
        e.get("type") == "agent_final"
        and e.get("text") == "I'll inspect the workspace files and summarize what I find."
        for e in events
    )
    assert any(
        e.get("type") == "agent_final"
        and e.get("round") == 2
        and e.get("text") == "Done: README.txt is present."
        for e in events
    )
    assert any(
        "unfinished promise or plan" in str(m.get("content", ""))
        for m in message_snapshots[1]
    )
    metrics = [e["data"] for e in events if e.get("type") == "metrics"][-1]
    assert metrics["agent_incomplete_final_recovery"]["nudges"] == 1


def test_incomplete_final_guard_accepts_honest_blocker(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        text = "I can't inspect the files because no workspace is attached."
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Inspect the workspace files."}],
        max_rounds=3,
        relevant_tools={"bash", "ls"},
    )
    events = _types(_collect(gen))

    assert calls == 1
    assert not any(e.get("type") == "agent_step" for e in events), events
    assert any(
        e.get("type") == "agent_final"
        and e.get("text") == "I can't inspect the files because no workspace is attached."
        for e in events
    )
    metrics = [e["data"] for e in events if e.get("type") == "metrics"][-1]
    assert "agent_incomplete_final_recovery" not in metrics


def test_empty_agent_round_retries_then_accepts_answer(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0
    message_snapshots = []

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        message_snapshots.append([dict(m) for m in messages])
        if calls == 1:
            yield "data: [DONE]\n\n"
            return
        yield f'data: {json.dumps({"delta": "Recovered answer."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Answer after an empty model response."}],
        max_rounds=3,
        relevant_tools={"bash"},
    )
    events = _types(_collect(gen))

    assert calls == 2
    assert any(e.get("type") == "agent_step" and e.get("round") == 2 for e in events), events
    assert any(
        "produced no user-visible answer" in str(m.get("content", ""))
        for m in message_snapshots[1]
    )
    assert any(
        e.get("type") == "agent_final"
        and e.get("round") == 2
        and e.get("text") == "Recovered answer."
        for e in events
    )
    metrics = [e["data"] for e in events if e.get("type") == "metrics"][-1]
    assert metrics["agent_empty_response_recovery"] == {
        "retries": 1,
        "max_retries": 2,
        "failed": False,
    }


def test_empty_agent_round_emits_failure_after_bounded_retries(monkeypatch):
    _patch_common(monkeypatch)
    calls = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal calls
        calls += 1
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "This model will stay empty."}],
        max_rounds=4,
        relevant_tools={"bash"},
    )
    events = _types(_collect(gen))

    assert calls == 3
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events
    failure = "The model returned an empty response"
    assert any(e.get("delta", "").startswith(failure) for e in events), events
    assert any(
        e.get("type") == "agent_final"
        and e.get("round") == 3
        and e.get("text", "").startswith(failure)
        for e in events
    )
    metrics = [e["data"] for e in events if e.get("type") == "metrics"][-1]
    assert metrics["agent_empty_response_recovery"] == {
        "retries": 2,
        "max_retries": 2,
        "failed": True,
    }
