"""Tests for the powershell agent tool."""
import sys
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# find_powershell
# ---------------------------------------------------------------------------

def test_find_powershell_prefers_pwsh():
    """find_powershell returns pwsh path when available."""
    from src.tool_execution import find_powershell
    with patch("src.tool_execution.shutil.which", side_effect=lambda x: "/usr/bin/pwsh" if x == "pwsh" else None):
        assert find_powershell() == "/usr/bin/pwsh"


def test_find_powershell_falls_back_to_powershell_exe():
    """find_powershell falls back to powershell when pwsh is absent."""
    from src.tool_execution import find_powershell
    with patch("src.tool_execution.shutil.which", side_effect=lambda x: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" if x == "powershell" else None):
        assert find_powershell() == "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"


def test_find_powershell_returns_none_when_absent():
    """find_powershell returns None when neither pwsh nor powershell is on PATH."""
    from src.tool_execution import find_powershell
    with patch("src.tool_execution.shutil.which", return_value=None):
        assert find_powershell() is None


# ---------------------------------------------------------------------------
# powershell dispatcher — not-found path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_powershell_tool_not_found():
    """powershell tool returns exit_code 1 when PowerShell is not installed."""
    from src.tool_execution import execute_tool
    with patch("src.tool_execution.find_powershell", return_value=None):
        result = await execute_tool("powershell", "Get-Date", session_id=None, workspace=None)
    assert result["exit_code"] == 1
    assert "not found" in result["output"].lower()


# ---------------------------------------------------------------------------
# powershell dispatcher — success path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_powershell_tool_runs_command():
    """powershell tool executes via pwsh -NonInteractive -Command and returns output."""
    from src.tool_execution import execute_tool

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    async def fake_streaming(proc, timeout, progress_cb):
        return ("Hello from PS", "", 0, False)

    with patch("src.tool_execution.find_powershell", return_value="/usr/bin/pwsh"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec, \
         patch("src.tool_execution._run_subprocess_streaming", side_effect=fake_streaming):
        result = await execute_tool("powershell", "Write-Output 'Hello from PS'", session_id=None, workspace=None)

    assert result["exit_code"] == 0
    assert "Hello from PS" in result["output"]
    # Must be called with -NonInteractive -Command
    args = mock_exec.call_args[0]
    assert "-NonInteractive" in args
    assert "-Command" in args


@pytest.mark.asyncio
async def test_powershell_tool_merges_stderr():
    """powershell tool merges stderr into output when present."""
    from src.tool_execution import execute_tool

    mock_proc = MagicMock()
    mock_proc.returncode = 1

    async def fake_streaming(proc, timeout, progress_cb):
        return ("", "some error", 1, False)

    with patch("src.tool_execution.find_powershell", return_value="/usr/bin/pwsh"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
         patch("src.tool_execution._run_subprocess_streaming", side_effect=fake_streaming):
        result = await execute_tool("powershell", "bad-command", session_id=None, workspace=None)

    assert result["exit_code"] == 1
    assert "some error" in result["output"]


@pytest.mark.asyncio
async def test_powershell_tool_timeout():
    """powershell tool returns exit_code 124 on timeout."""
    from src.tool_execution import execute_tool

    mock_proc = MagicMock()
    mock_proc.returncode = -1

    async def fake_streaming(proc, timeout, progress_cb):
        return ("", "", -1, True)  # timed_out=True

    with patch("src.tool_execution.find_powershell", return_value="/usr/bin/pwsh"), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
         patch("src.tool_execution._run_subprocess_streaming", side_effect=fake_streaming):
        result = await execute_tool("powershell", "Start-Sleep 999", session_id=None, workspace=None)

    assert result["exit_code"] == 124
    assert "timed out" in result["error"].lower()
