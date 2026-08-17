"""Propagation tests for the per-turn Bubblewrap network policy."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.agent_loop as agent_loop
import src.tool_execution as tool_execution
from src.agent_tools import ToolBlock
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


@pytest.mark.asyncio
async def test_tool_executor_forwards_network_policy_to_subprocess_fallback(monkeypatch):
    seen = []

    async def fake_direct_fallback(tool, content, **kwargs):
        seen.append((tool, content, kwargs.get("allow_network")))
        return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(tool_execution, "_direct_fallback", fake_direct_fallback)

    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf ok"),
        owner="admin",
        allow_network=True,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["exit_code"] == 0
    assert seen == [("bash", "printf ok", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["bash", "python"])
async def test_subprocess_handlers_apply_network_policy(monkeypatch, tool_name):
    import src.agent_tools.subprocess_tools as subprocess_tools

    sandbox_calls = []

    def fake_sandbox_command(command, **kwargs):
        sandbox_calls.append((command, kwargs))
        return ["/usr/bin/true"]

    async def fake_create_subprocess_exec(*args, **kwargs):
        return object()

    async def fake_run_subprocess_streaming(*args, **kwargs):
        return "", "", 0, False

    monkeypatch.setattr(subprocess_tools, "sandbox_command", fake_sandbox_command)
    monkeypatch.setattr(
        subprocess_tools.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        subprocess_tools,
        "_run_subprocess_streaming",
        fake_run_subprocess_streaming,
    )

    handler = (
        subprocess_tools.BashTool()
        if tool_name == "bash"
        else subprocess_tools.PythonTool()
    )
    result = await handler.execute("printf ok", {"allow_network": True})

    assert result["exit_code"] == 0
    assert sandbox_calls[0][1]["allow_network"] is True


@pytest.mark.asyncio
async def test_background_bash_inherits_network_policy(monkeypatch):
    seen = []

    def fake_launch(command, **kwargs):
        seen.append((command, kwargs))
        return {"id": "job-1"}

    from src import bg_jobs

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(bg_jobs, "launch", fake_launch)

    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "#!bg\nprintf networked"),
        session_id="session-1",
        owner="admin",
        workspace="/tmp/workspace",
        allow_network=True,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["bg_job_id"] == "job-1"
    assert seen == [
        (
            "printf networked",
            {
                "session_id": "session-1",
                "cwd": "/tmp/workspace",
                "allow_network": True,
            },
        )
    ]


def test_agent_loop_forwards_network_policy_to_every_tool_call(monkeypatch):
    calls = []
    round_number = 0

    monkeypatch.setattr(agent_loop, "get_setting", lambda key, default=None: default)
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())

    async def fake_stream(_candidates, messages, **kwargs):
        nonlocal round_number
        round_number += 1
        if round_number == 1:
            call = {
                "name": "bash",
                "arguments": json.dumps({"command": "printf ok"}),
            }
            yield "data: " + json.dumps({"type": "tool_calls", "calls": [call]}) + "\n\n"
        else:
            yield "data: " + json.dumps({"delta": "done"}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def fake_execute(block, **kwargs):
        calls.append((block.tool_type, kwargs.get("allow_network")))
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    _collect(
        agent_loop.stream_agent_loop(
            "https://model.example/v1",
            "test-model",
            [{"role": "user", "content": "Run the command."}],
            owner="admin",
            max_rounds=2,
            relevant_tools={"bash"},
            allow_network=True,
            _is_teacher_run=True,
        )
    )

    assert calls == [("bash", True)]


def test_background_followup_preserves_originating_network_policy(monkeypatch):
    from src import bg_monitor

    seen = []

    async def fake_stream_agent_loop(*args, **kwargs):
        seen.append(kwargs.get("allow_network"))
        yield "data: [DONE]"

    monkeypatch.setattr(agent_loop, "stream_agent_loop", fake_stream_agent_loop)
    session = SimpleNamespace(
        endpoint_url="https://model.example/v1",
        model="test-model",
        headers=None,
        context_length=0,
        id="session-1",
        owner="admin",
    )

    asyncio.run(bg_monitor._drain_agent(session, [], allow_network=True))

    assert seen == [True]
