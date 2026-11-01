"""Windows execution contract for the agent Bash tool."""

import asyncio

import pytest
from types import SimpleNamespace

from src.agent_tools import subprocess_tools
from src import containment
from tests.containment_helpers import capture_owned_spawn
from tests.process_resource_helpers import authorized_handler


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
    assert captured["argv"] == (bash, "-c", "pwd; cat package.json")
    assert captured["kwargs"]["cwd"] == workspace


@pytest.mark.asyncio
async def test_windows_bash_captures_output_instead_of_inheriting_server_handles(monkeypatch):
    captured = {}

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_tools, "find_bash", lambda: r"C:\Program Files\Git\bin\bash.exe"
    )

    async def fake_exec(*_argv, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)

    await subprocess_tools._create_bash_subprocess("pwd", cwd=r"C:\Work")

    assert captured["stdout"] == asyncio.subprocess.PIPE
    assert captured["stderr"] == asyncio.subprocess.PIPE
    assert captured["stdin"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_windows_bash_applies_the_subprocess_env(monkeypatch):
    captured = {}
    env = {"PATH": r"C:\Odysseus\venv\Scripts", "HOME": r"C:\Odysseus\data"}

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_tools, "find_bash", lambda: r"C:\Program Files\Git\bin\bash.exe"
    )

    async def fake_exec(*_argv, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)

    await subprocess_tools._create_bash_subprocess("pwd", cwd=r"C:\Work", env=env)

    assert captured["env"] == env


@pytest.mark.asyncio
async def test_windows_bash_tool_passes_ctx_env_through_to_the_child(monkeypatch, tmp_path):
    captured = capture_owned_spawn(monkeypatch, tmp_path)
    env = {"PATH": r"C:\Odysseus\venv\Scripts", "VIRTUAL_ENV": r"C:\Odysseus\venv"}

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_tools, "find_bash", lambda: r"C:\Program Files\Git\bin\bash.exe"
    )
    monkeypatch.setattr(containment, "IS_WINDOWS", True)
    monkeypatch.setattr(containment, "find_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    result = await authorized_handler(subprocess_tools.BashTool().execute, tmp_path)(
        "pwd",
        {"subproc_env": env, "session_id": "chat-1"},
    )

    assert result["output"] == "ok"
    assert result["exit_code"] == 0
    assert "containment" in result
    assert captured["kwargs"]["env"] == env
    assert captured["kwargs"]["stdout"] == asyncio.subprocess.PIPE
    assert captured["kwargs"]["stderr"] == asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_windows_bash_without_git_bash_fails_clearly(monkeypatch):
    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: None)

    async def fail_spawn(*_args, **_kwargs):
        pytest.fail("no subprocess should start without Git Bash")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fail_spawn)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fail_spawn)

    with pytest.raises(RuntimeError, match="Git Bash is required"):
        await subprocess_tools._create_bash_subprocess("pwd", cwd=r"C:\Work")


@pytest.mark.asyncio
async def test_bash_tool_returns_install_hint_when_git_bash_is_missing(monkeypatch, tmp_path):
    capture_owned_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(subprocess_tools, "find_bash", lambda: None)
    monkeypatch.setattr(containment, "IS_WINDOWS", True)
    monkeypatch.setattr(containment, "find_bash", lambda: None)
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    result = await authorized_handler(subprocess_tools.BashTool().execute, tmp_path)(
        "pwd",
        {"subproc_env": {}, "session_id": None},
    )

    assert result["exit_code"] == 1
    assert "install Git for Windows" in result["error"]


@pytest.mark.asyncio
async def test_windows_bash_does_not_use_a_stray_tmux_executable(monkeypatch, tmp_path):
    captured = capture_owned_spawn(monkeypatch, tmp_path)
    workspace = str(tmp_path)

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(containment, "IS_WINDOWS", True)
    monkeypatch.setattr(containment, "find_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
    monkeypatch.setattr(
        subprocess_tools.shutil,
        "which",
        lambda name: r"C:\msys64\usr\bin\tmux.exe",
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: workspace)

    async def fail_tmux(*_args, **_kwargs):
        pytest.fail("native Windows must not enter the POSIX tmux path")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fail_tmux)

    result = await authorized_handler(subprocess_tools.BashTool().execute, workspace)(
        "pwd",
        {"subproc_env": {}, "session_id": "chat-1"},
    )

    assert result["output"] == "ok"
    assert result["exit_code"] == 0
    # Every bash result now carries the execution boundary it actually got.
    # Asserting dict equality here would make that field impossible to add
    # without touching a test about tmux, so the shape is asserted instead.
    assert result["containment"]["network"] == "inherit"
    assert captured["command"] == "pwd"
    assert captured["kwargs"]["cwd"] == workspace


@pytest.mark.asyncio
async def test_posix_bash_keeps_existing_shell_path(monkeypatch):
    captured = {}
    process = object()

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", False)

    async def fake_shell(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    async def fail_exec(*_args, **_kwargs):
        pytest.fail("POSIX behavior must continue through create_subprocess_shell")

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fail_exec)

    result = await subprocess_tools._create_bash_subprocess("pwd", cwd="/tmp/work")

    assert result is process
    assert captured == {"command": "pwd", "kwargs": {"cwd": "/tmp/work"}}
