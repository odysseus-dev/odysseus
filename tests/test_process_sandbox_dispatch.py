"""Regression coverage for native process-tool dispatch."""

import asyncio
import threading
from types import SimpleNamespace

import pytest


class _FailingMcpManager:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        raise AssertionError("process tools must not reach MCP")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["bash", "python"])
async def test_process_tools_use_native_handler_context(monkeypatch, tool_name):
    import src.agent_tools as agent_tools
    import src.tool_execution as tool_execution

    manager = _FailingMcpManager()
    seen = {}

    async def fake_handler(content, ctx):
        seen["content"] = content
        seen["ctx"] = ctx
        return {"output": "native", "exit_code": 0}

    progress_cb = object()
    network_profile = tool_execution.SandboxNetworkProfile.BROKERED_ONLY
    monkeypatch.setattr(tool_execution, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)
    monkeypatch.setattr(tool_execution, "is_public_blocked_tool", lambda _tool: False)
    monkeypatch.setitem(agent_tools.TOOL_HANDLERS, tool_name, fake_handler)

    _, result = await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type=tool_name, content="printf native"),
        session_id="chat-5818",
        owner="alice",
        progress_cb=progress_cb,
        network_profile=network_profile,
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
    )

    assert result == {"output": "native", "exit_code": 0}
    assert seen == {
        "content": "printf native",
        "ctx": {
            "progress_cb": progress_cb,
            "session_id": "chat-5818",
            "owner": "alice",
            "network_profile": network_profile,
        },
    }
    assert manager.calls == []


def test_process_tools_are_not_mcp_registered():
    import src.tool_execution as tool_execution

    assert tool_execution._PROCESS_TOOLS == frozenset({"bash", "python"})
    assert "bash" not in tool_execution._MCP_TOOL_MAP
    assert "python" not in tool_execution._MCP_TOOL_MAP
    assert "bash" not in tool_execution._MCP_ARG_PARSERS
    assert "python" not in tool_execution._MCP_ARG_PARSERS


@pytest.mark.asyncio
async def test_foreground_bash_with_session_reaches_tmux(monkeypatch, tmp_path):
    import src.agent_tools as agent_tools
    import src.agent_tools.subprocess_tools as subprocess_tools
    import src.tool_execution as tool_execution

    seen = {}

    async def fake_run_tmux_bash(content, **kwargs):
        seen["content"] = content
        seen["kwargs"] = kwargs
        return "tmux output", "", 0, False, "ody-agent-sbx-v2-test"

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    monkeypatch.setattr(tool_execution, "is_public_blocked_tool", lambda _tool: False)
    monkeypatch.setattr(subprocess_tools.shutil, "which", lambda _name: "/usr/bin/tmux")
    monkeypatch.setattr(subprocess_tools, "_run_tmux_bash", fake_run_tmux_bash)
    monkeypatch.setitem(
        agent_tools.TOOL_HANDLERS,
        "bash",
        subprocess_tools.BashTool().execute,
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _, result = await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type="bash", content="printf tmux"),
        session_id="chat-5818",
        owner="alice",
        workspace=str(workspace),
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["exit_code"] == 0
    assert result["output"] == "tmux output"
    assert result["tmux_session"] == "ody-agent-sbx-v2-test"
    assert seen == {
        "content": "printf tmux",
        "kwargs": {
            "session_id": "chat-5818",
            "cwd": str(workspace),
            "timeout": subprocess_tools.DEFAULT_BASH_TIMEOUT,
            "progress_cb": None,
            "network_profile": tool_execution.SandboxNetworkProfile.NETWORKLESS,
        },
    }


@pytest.mark.asyncio
async def test_background_bash_launches_before_foreground_dispatch(monkeypatch):
    import src.bg_jobs as bg_jobs
    import src.tool_execution as tool_execution

    seen = {}

    def fake_launch(command, **kwargs):
        seen["launch"] = (command, kwargs)
        return {"id": "job-5818"}

    async def forbidden_fallback(*_args, **_kwargs):
        raise AssertionError("background bash must return before foreground dispatch")

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    monkeypatch.setattr(bg_jobs, "launch", fake_launch)
    monkeypatch.setattr(tool_execution, "_direct_fallback", forbidden_fallback)

    _, result = await tool_execution.execute_tool_block(
        SimpleNamespace(tool_type="bash", content="#!bg\nprintf background"),
        session_id="chat-5818",
        owner="alice",
        workspace="/tmp/workspace",
        security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
    )

    assert result["bg_job_id"] == "job-5818"
    assert seen["launch"] == (
        "printf background",
        {
            "session_id": "chat-5818",
            "cwd": "/tmp/workspace",
            "network_profile": tool_execution.SandboxNetworkProfile.NETWORKLESS,
        },
    )


@pytest.mark.asyncio
async def test_background_policy_construction_does_not_block_request_loop(monkeypatch):
    import src.bg_jobs as bg_jobs
    import src.tool_execution as tool_execution

    started = threading.Event()
    release = threading.Event()

    def delayed_launch(*_args, **_kwargs):
        started.set()
        if not release.wait(timeout=2):
            raise AssertionError("event loop did not remain available during launch")
        return {"id": "job-async", "execution_mode": "sandbox"}

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    monkeypatch.setattr(bg_jobs, "launch", delayed_launch)

    task = asyncio.create_task(
        tool_execution.execute_tool_block(
            SimpleNamespace(tool_type="bash", content="#!bg\nprintf background"),
            session_id="chat-5818",
            owner="alice",
            workspace="/tmp/workspace",
            security_context=tool_execution.NO_TOOL_SECURITY_CONTEXT,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        assert not task.done()
    finally:
        release.set()

    _, result = await asyncio.wait_for(task, timeout=1)
    assert result["bg_job_id"] == "job-async"
