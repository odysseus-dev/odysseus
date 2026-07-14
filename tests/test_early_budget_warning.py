"""Focused regression coverage for the agent's early round-budget warning."""

import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)

    async def _fake_exec(block, *args, **kwargs):
        return ("bash", {"output": "ok", "exit_code": 0})

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_tool_loop(monkeypatch, max_rounds):
    seen_messages = []
    call_number = 0

    async def _fake_stream(_candidates, messages, **kwargs):
        nonlocal call_number
        call_number += 1
        seen_messages.append([dict(message) for message in messages])
        call = {
            "id": f"call_{call_number}",
            "name": "bash",
            "arguments": json.dumps({"command": f"echo {call_number}"}),
        }
        yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    _collect(
        al.stream_agent_loop(
            "http://x/v1",
            "m",
            [{"role": "user", "content": "do a long task"}],
            max_rounds=max_rounds,
            relevant_tools={"bash"},
        )
    )
    return seen_messages


def test_warning_round_is_eighty_percent_for_normal_budget():
    assert al._early_budget_warning_round(50) == 40


def test_small_budgets_warn_before_exhaustion_where_possible():
    assert al._early_budget_warning_round(1) == 1
    assert al._early_budget_warning_round(2) == 1
    assert al._early_budget_warning_round(3) == 2


def test_warning_is_injected_once_before_threshold_request(monkeypatch):
    _patch_common(monkeypatch)
    seen = _run_tool_loop(monkeypatch, max_rounds=6)
    warning_round = al._early_budget_warning_round(6)
    assert len(seen) == 6
    counts = [
        sum(message.get("content") == al._EARLY_BUDGET_WARNING for message in request)
        for request in seen
    ]
    assert counts[: warning_round - 1] == [0] * (warning_round - 1)
    assert counts[warning_round - 1 :] == [1] * (len(seen) - warning_round + 1)
    warning = next(
        message
        for message in seen[warning_round - 1]
        if message.get("content") == al._EARLY_BUDGET_WARNING
    )
    assert warning["role"] == "system"


def test_completion_before_threshold_adds_no_warning(monkeypatch):
    _patch_common(monkeypatch)
    seen = []

    async def _fake_stream(_candidates, messages, **kwargs):
        seen.append([dict(message) for message in messages])
        yield f'data: {json.dumps({"delta": "All requirements are complete."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    _collect(
        al.stream_agent_loop(
            "http://x/v1",
            "m",
            [{"role": "user", "content": "do a task"}],
            max_rounds=50,
            relevant_tools={"bash"},
        )
    )
    assert len(seen) == 1
    assert all(
        message.get("content") != al._EARLY_BUDGET_WARNING
        for message in seen[0]
    )


def test_warning_does_not_change_hard_round_limit(monkeypatch):
    _patch_common(monkeypatch)
    seen = _run_tool_loop(monkeypatch, max_rounds=3)
    assert len(seen) == 3
