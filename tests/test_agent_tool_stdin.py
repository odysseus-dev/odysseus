"""Agent bash/python tools must not inherit the server's stdin.

When Odysseus is started from a terminal (launch-windows.ps1, a manual
uvicorn run) fd 0 is that console. A child that reads stdin -- `cat`,
`input()`, or `cmd /c dir` under Git Bash, which MSYS turns into an
interactive cmd.exe -- then waits for keystrokes nobody will send until the
one-hour tool timeout.
"""

import asyncio
import os
import sys
from contextlib import contextmanager

import pytest

from core.platform_compat import find_bash
from src.agent_tools import subprocess_tools


@contextmanager
def _stdin_held_open():
    """Point fd 0 at a pipe that never reaches EOF, like an idle console."""
    read_fd, write_fd = os.pipe()
    saved = os.dup(0)
    os.dup2(read_fd, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_fd, write_fd):
            os.close(fd)


@pytest.mark.asyncio
async def test_bash_tool_does_not_inherit_stdin(monkeypatch):
    captured = {}

    async def fake_create(command, **kwargs):
        captured.update(kwargs)
        return object()

    async def fake_stream(_process, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(subprocess_tools, "_create_bash_subprocess", fake_create)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    await subprocess_tools.BashTool().execute("cat", {"subproc_env": {}, "session_id": None})

    assert captured["stdin"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_python_tool_does_not_inherit_stdin(monkeypatch):
    captured = {}

    async def fake_exec(*_argv, **kwargs):
        captured.update(kwargs)
        return object()

    async def fake_stream(_process, **_kwargs):
        return "", "", 0, False

    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess_tools, "_run_subprocess_streaming", fake_stream)

    await subprocess_tools.PythonTool().execute("input()", {"subproc_env": None})

    assert captured["stdin"] == asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
async def test_python_input_fails_fast_with_open_console_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    with _stdin_held_open():
        result = await asyncio.wait_for(
            subprocess_tools.PythonTool().execute("input()", {"subproc_env": None}),
            timeout=30,
        )
    assert result["exit_code"] != 0
    assert "EOFError" in result["output"]


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.platform == "win32" and not find_bash(),
    reason="the Windows bash tool needs Git Bash",
)
async def test_bash_cat_returns_with_open_console_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    with _stdin_held_open():
        result = await asyncio.wait_for(
            subprocess_tools.BashTool().execute(
                "cat; echo done", {"subproc_env": None, "session_id": None}
            ),
            timeout=30,
        )
    assert result == {"output": "done", "exit_code": 0}
