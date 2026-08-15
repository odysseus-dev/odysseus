"""Windows execution contract for the agent Bash tool."""

import pytest

from src.agent_tools import subprocess_tools


@pytest.mark.asyncio
async def test_windows_bash_uses_git_bash_with_structural_cwd(monkeypatch):
    captured = {}
    bash = r"C:\Program Files\Git\bin\bash.exe"
    workspace = r"D:\Workspaces\Project with spaces"
    process = object()

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: bash)

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    async def fail_shell(*_args, **_kwargs):
        pytest.fail("native Windows Bash must not execute through cmd.exe")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fail_shell)

    result = await subprocess_tools._create_bash_subprocess(
        "pwd; cat package.json",
        cwd=workspace,
        env={"HOME": r"C:\Odysseus\data"},
    )

    assert result is process
    assert captured["argv"] == (
        bash, "--noprofile", "--norc", "-c", "pwd; cat package.json"
    )
    assert captured["kwargs"]["cwd"] == workspace


@pytest.mark.asyncio
async def test_windows_bash_without_git_bash_fails_clearly(monkeypatch):
    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: None)

    async def fail_spawn(*_args, **_kwargs):
        pytest.fail("no subprocess should start without Git Bash")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fail_spawn)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fail_spawn)

    with pytest.raises(RuntimeError, match="install Git for Windows"):
        await subprocess_tools._create_bash_subprocess("pwd", cwd=r"C:\Work")


@pytest.mark.asyncio
async def test_bash_tool_returns_install_hint_when_git_bash_is_missing(monkeypatch):
    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: None)

    result = await subprocess_tools.BashTool().execute(
        "pwd",
        {"subproc_env": {}, "session_id": None},
    )

    assert result["exit_code"] == 1
    assert "install Git for Windows" in result["error"]


@pytest.mark.asyncio
async def test_windows_bash_session_id_does_not_change_execution_path(monkeypatch):
    captured = {}
    workspace = r"D:\Workspaces\Project with spaces"

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: workspace)

    async def fake_create(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return object()

    async def fake_stream(_process, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(subprocess_tools, "_create_bash_subprocess", fake_create)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    result = await subprocess_tools.BashTool().execute(
        "pwd",
        {"subproc_env": {}, "session_id": "chat-1"},
    )

    assert result == {
        "output": "ok",
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
    }
    assert captured["command"] == "pwd"
    assert captured["kwargs"]["cwd"] == workspace


@pytest.mark.asyncio
async def test_posix_bash_uses_explicit_bash(monkeypatch):
    captured = {}
    process = object()

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", False)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: "/usr/bin/bash")

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    async def fail_shell(*_args, **_kwargs):
        pytest.fail("the Bash tool must never delegate to /bin/sh")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fail_shell)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)

    result = await subprocess_tools._create_bash_subprocess("pwd", cwd="/tmp/work")

    assert result is process
    assert captured["argv"] == (
        "/usr/bin/bash", "--noprofile", "--norc", "-c", "pwd"
    )
    assert captured["kwargs"]["cwd"] == "/tmp/work"
    assert captured["kwargs"]["start_new_session"] is True
