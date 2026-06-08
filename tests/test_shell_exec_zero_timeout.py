"""`_exec_shell` must treat a falsy timeout as "no limit".

The /api/shell/exec request model documents `0 = no timeout (run until client
disconnects)`, and the PTY stream path already uses `if timeout` for its
deadline. But `_exec_shell` passed the timeout straight to
`asyncio.wait_for(..., timeout=0)`, which times out immediately — so a
deliberate `timeout=0` returned "Command timed out after 0s" instead of running.
"""
import asyncio

from routes.shell_routes import _exec_shell


def test_zero_timeout_runs_to_completion():
    result = asyncio.run(_exec_shell("echo hi", timeout=0))
    assert result["exit_code"] == 0, result
    assert "hi" in result["stdout"]


def test_positive_timeout_still_enforced():
    # A real timeout must still fire (sleep longer than the limit).
    result = asyncio.run(_exec_shell("sleep 2", timeout=1))
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"].lower()
