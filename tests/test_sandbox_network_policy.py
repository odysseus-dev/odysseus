"""Propagation tests for the per-turn Bubblewrap network policy."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import src.agent_loop as agent_loop
import src.tool_execution as tool_execution
from src.agent_tools import ToolBlock
from src.execution_sandbox import (
    SandboxNetworkProfile,
    network_profile_from_snapshot,
)
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]

    return asyncio.run(_run())


@pytest.mark.asyncio
async def test_tool_executor_forwards_network_policy_to_subprocess_fallback(monkeypatch):
    seen = []

    async def fake_direct_fallback(tool, content, **kwargs):
        seen.append((tool, content, kwargs.get("network_profile")))
        return {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(tool_execution, "_direct_fallback", fake_direct_fallback)

    _, result = await tool_execution.execute_tool_block(
        ToolBlock("bash", "printf ok"),
        owner="admin",
        network_profile=SandboxNetworkProfile.BROKERED_ONLY,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["exit_code"] == 0
    assert seen == [
        ("bash", "printf ok", SandboxNetworkProfile.BROKERED_ONLY)
    ]


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
    result = await handler.execute(
        "printf ok",
        {"network_profile": SandboxNetworkProfile.BROKERED_ONLY},
    )

    assert result["exit_code"] == 0
    assert (
        sandbox_calls[0][1]["network_profile"]
        is SandboxNetworkProfile.BROKERED_ONLY
    )


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
        network_profile=SandboxNetworkProfile.BROKERED_ONLY,
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["bg_job_id"] == "job-1"
    assert seen == [
        (
            "printf networked",
                {
                    "session_id": "session-1",
                    "cwd": "/tmp/workspace",
                    "execution_profile": "workspace_sandbox",
                    "network_profile": SandboxNetworkProfile.BROKERED_ONLY,
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
        calls.append((block.tool_type, kwargs.get("network_profile")))
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
            network_profile=SandboxNetworkProfile.BROKERED_ONLY,
            _is_teacher_run=True,
        )
    )

    assert calls == [("bash", SandboxNetworkProfile.BROKERED_ONLY)]


def test_background_followup_preserves_originating_network_policy(monkeypatch):
    from src import bg_monitor

    seen = []

    async def fake_stream_agent_loop(*args, **kwargs):
        seen.append(kwargs.get("network_profile"))
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

    asyncio.run(
        bg_monitor._drain_agent(
            session,
            [],
            network_profile=SandboxNetworkProfile.BROKERED_ONLY,
        )
    )

    assert seen == [SandboxNetworkProfile.BROKERED_ONLY]


def test_invalid_persisted_profile_fails_back_to_networkless():
    assert (
        network_profile_from_snapshot("open")
        is SandboxNetworkProfile.NETWORKLESS
    )


def test_network_profile_has_no_raw_open_mode():
    assert {profile.value for profile in SandboxNetworkProfile} == {
        "networkless",
        "brokered_only",
    }


def test_launch_snapshot_does_not_follow_a_later_toggle_change():
    launched_record = {
        "network_profile": SandboxNetworkProfile.BROKERED_ONLY.value,
    }
    current_selection = SandboxNetworkProfile.NETWORKLESS

    assert current_selection is SandboxNetworkProfile.NETWORKLESS
    assert (
        network_profile_from_snapshot(launched_record["network_profile"])
        is SandboxNetworkProfile.BROKERED_ONLY
    )


@pytest.mark.asyncio
async def test_tmux_launch_scrubs_the_server_environment(monkeypatch):
    import src.agent_tools.subprocess_tools as subprocess_tools

    session_checks = iter((False, True))
    calls = []

    async def fake_has_session(_name):
        return next(session_checks)

    async def fake_run_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return "", "", 0

    monkeypatch.setattr(subprocess_tools, "_tmux_has_session", fake_has_session)
    monkeypatch.setattr(subprocess_tools, "_run_exec", fake_run_exec)
    shell_argv = [
        "/usr/local/libexec/odysseus-seccomp-launcher",
        "/usr/bin/bwrap",
        "--",
        "/bin/bash",
    ]

    await subprocess_tools._ensure_tmux_session(
        "session",
        "/workspace",
        shell_argv,
    )

    launch = calls[0][0]
    scrubber = launch.index("/usr/bin/env")
    assert launch[scrubber:scrubber + 2] == ("/usr/bin/env", "-i")
    assert launch[scrubber + 2:] == tuple(shell_argv)


@pytest.mark.asyncio
async def test_tmux_client_does_not_update_server_from_application_env(monkeypatch):
    import src.agent_tools.subprocess_tools as subprocess_tools

    captured = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(
        subprocess_tools.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    assert await subprocess_tools._run_exec("tmux", "-V") == ("", "", 0)
    assert captured["env"] == {}
