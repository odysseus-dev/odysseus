"""Integration test: the mode permission gate fires inside the real agent loop.

We mock only the seams — the model stream, the tool executor, the tool-block
resolver, and the result formatter — then drive stream_agent_loop in each mode
and assert the gate's behavior. No real model, no network, no UI.

Mocking the resolver + formatter (not just the stream/executor) keeps this test
independent of `src.agent_tools` being swapped for a MagicMock by another test
module (test_agent_loop.py poisons sys.modules), so it passes in the full suite.
"""
import asyncio
import collections
import importlib
import json
import unittest.mock as _mock

from src import agent_loop, approvals


def _ensure_real_agent_loop():
    """test_agent_loop.py (collected earlier in the full suite) swaps
    src.agent_tools for a MagicMock and never restores it, so agent_loop's
    imported helpers (strip_tool_blocks, format_tool_result, ...) become mocks
    and this REAL-loop test breaks. Detect that, drop the mocked modules, and
    reload agent_loop against the real ones."""
    suspects = ("strip_tool_blocks", "format_tool_result", "parse_tool_blocks",
                "function_call_to_tool_block", "execute_tool_block")
    if not any(isinstance(getattr(agent_loop, _n, None), _mock.MagicMock) for _n in suspects):
        return
    import sys
    for _name in list(sys.modules):
        if isinstance(sys.modules.get(_name), _mock.MagicMock):
            del sys.modules[_name]
    importlib.reload(agent_loop)


_ensure_real_agent_loop()

_Block = collections.namedtuple("ToolBlock", ["tool_type", "content"])


def _fmt(desc, result):
    return f"{desc}: {result.get('output') or result.get('error') or 'ok'}"


def _run_turn(monkeypatch, mode, tool, args, decision=None):
    """Drive one agent turn that emits a single `tool` call. Returns
    (events, executed) where executed lists tools that reached the executor.
    If `decision` is set (True/False), resolve any approval prompt with it."""
    executed = []

    async def fake_stream(_candidates, messages, **kw):
        yield "data: [DONE]\n\n"

    async def fake_exec(block, **kw):
        executed.append(block.tool_type)
        return (f"{block.tool_type} ran", {"output": "ok", "exit_code": 0})

    def fake_resolve(round_response, native_tool_calls, round_num):
        return ([_Block(tool, json.dumps(args))], True)

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_exec)
    monkeypatch.setattr(agent_loop, "_resolve_tool_blocks", fake_resolve)
    monkeypatch.setattr(agent_loop, "format_tool_result", _fmt)

    events = []

    async def go():
        gen = agent_loop.stream_agent_loop(
            "http://local/v1", "test-model",
            [{"role": "user", "content": "do it"}],
            mode=mode, max_rounds=1, session_id="t1",
        )
        async for ch in gen:
            events.append(ch)
            if decision is not None and '"approval_required"' in ch:
                d = json.loads(ch[6:])
                approvals.resolve(d["id"], approved=decision)

    asyncio.run(asyncio.wait_for(go(), timeout=10))
    return events, executed


def _blocked(events):
    return any('"blocked": true' in e for e in events)


def _approval_asked(events):
    return any('"approval_required"' in e for e in events)


def test_plan_blocks_a_write(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "plan", "write_file", {"path": "a", "content": "b"})
    assert ex == [] and _blocked(ev) and not _approval_asked(ev)


def test_agent_runs_a_write(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "agent", "write_file", {"path": "a", "content": "b"})
    assert ex == ["write_file"] and not _approval_asked(ev)


def test_accept_edits_auto_runs_an_edit(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "accept_edits", "create_document", {"title": "T", "content": "b"})
    assert ex == ["create_document"] and not _approval_asked(ev)


def test_accept_edits_prompts_for_a_mutation(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "accept_edits", "bash", {"command": "ls"}, decision=True)
    assert _approval_asked(ev) and ex == ["bash"]


def test_manual_approve_runs(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "manual", "write_file", {"path": "a", "content": "b"}, decision=True)
    assert _approval_asked(ev) and ex == ["write_file"]


def test_manual_deny_blocks(monkeypatch):
    ev, ex = _run_turn(monkeypatch, "manual", "write_file", {"path": "a", "content": "b"}, decision=False)
    assert _approval_asked(ev) and ex == [] and _blocked(ev)
