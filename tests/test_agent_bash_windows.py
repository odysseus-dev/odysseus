"""Windows execution contract for the agent shell tool."""

import pytest

from core import platform_compat
from src.agent_tools import subprocess_tools


@pytest.mark.asyncio
async def test_windows_shell_uses_native_argv_with_structural_cwd(monkeypatch):
    captured = {}
    workspace = r"D:\Workspaces\Project with spaces"
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_tools,
        "native_shell_argv",
        lambda command: [powershell, "-NoProfile", "-Command", command],
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: workspace)

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    async def fake_stream(_process, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    result = await subprocess_tools.BashTool().execute(
        "Get-Location; Get-Content package.json",
        {"subproc_env": {}, "session_id": None},
    )

    assert captured["argv"] == (
        powershell,
        "-NoProfile",
        "-Command",
        "Get-Location; Get-Content package.json",
    )
    assert captured["kwargs"]["cwd"] == workspace
    assert result == {"output": "ok", "exit_code": 0, "shell": "powershell"}


def test_windows_native_argv_falls_back_to_cmd(monkeypatch):
    monkeypatch.setattr(platform_compat, "IS_WINDOWS", True)
    monkeypatch.setattr(platform_compat, "find_powershell", lambda: None)
    monkeypatch.setenv("ComSpec", r"C:\Windows\System32\cmd.exe")

    assert platform_compat.native_shell_argv("dir") == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/s",
        "/c",
        "dir",
    ]


@pytest.mark.asyncio
async def test_windows_shell_does_not_use_a_stray_tmux_executable(monkeypatch):
    captured = {}
    workspace = r"D:\Workspaces\Project with spaces"

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", True)
    monkeypatch.setattr(
        subprocess_tools.shutil,
        "which",
        lambda name: r"C:\msys64\usr\bin\tmux.exe",
    )
    monkeypatch.setattr(
        subprocess_tools,
        "native_shell_argv",
        lambda command: ["powershell.exe", "-Command", command],
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: workspace)

    async def fail_tmux(*_args, **_kwargs):
        pytest.fail("native Windows must not enter the POSIX tmux path")

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    async def fake_stream(_process, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(subprocess_tools, "_run_tmux_bash", fail_tmux)
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    result = await subprocess_tools.BashTool().execute(
        "Get-Location",
        {"subproc_env": {}, "session_id": "chat-1"},
    )

    assert result == {"output": "ok", "exit_code": 0, "shell": "powershell"}
    assert captured["argv"] == ("powershell.exe", "-Command", "Get-Location")
    assert captured["kwargs"]["cwd"] == workspace


@pytest.mark.asyncio
async def test_posix_shell_uses_native_bash_argv(monkeypatch):
    captured = {}

    monkeypatch.setattr(subprocess_tools, "IS_WINDOWS", False)
    monkeypatch.setattr(
        subprocess_tools,
        "native_shell_argv",
        lambda command: ["/bin/bash", "-lc", command],
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: "/tmp/work")

    async def fake_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    async def fake_stream(_process, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    result = await subprocess_tools.BashTool().execute(
        "pwd",
        {"subproc_env": {}, "session_id": None},
    )

    assert captured["argv"] == ("/bin/bash", "-lc", "pwd")
    assert captured["kwargs"]["cwd"] == "/tmp/work"
    assert result == {"output": "ok", "exit_code": 0, "shell": "bash"}
