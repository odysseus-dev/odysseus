"""`_exec_shell` timeout semantics.

The /api/shell/exec request model documents `0 = no timeout (run until client
disconnects)`. The buffered handler used to pass the timeout straight to
`asyncio.wait_for(..., timeout=0)`, which timed out immediately. The fix runs the
command without a fixed deadline when the timeout is falsy, but must still end on
client disconnect (Starlette does not cancel a buffered request coroutine when
the client goes away) so a non-terminating command cannot hang the worker or
orphan the subprocess.
"""
import asyncio
import time

from routes.shell_routes import _exec_shell


class _FakeRequest:
    def __init__(self, disconnected: bool):
        self._disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self._disconnected


def test_zero_timeout_runs_to_completion():
    result = asyncio.run(_exec_shell("echo hi", timeout=0))
    assert result["exit_code"] == 0, result
    assert "hi" in result["stdout"]


def test_positive_timeout_still_enforced():
    result = asyncio.run(_exec_shell("sleep 2", timeout=1))
    assert result["exit_code"] == -1
    assert "timed out" in result["stderr"].lower()


def test_zero_timeout_cancels_on_client_disconnect():
    # No fixed timeout, but the client has gone away: the command must be killed
    # and the call must return promptly instead of hanging forever.
    req = _FakeRequest(disconnected=True)
    t0 = time.monotonic()
    result = asyncio.run(_exec_shell("sleep 30", timeout=0, request=req))
    elapsed = time.monotonic() - t0
    assert result["exit_code"] == -1, result
    assert "disconnect" in result["stderr"].lower()
    assert elapsed < 10, f"should return promptly on disconnect, took {elapsed:.1f}s"


def test_zero_timeout_connected_client_runs_to_completion():
    # Still-connected client + falsy timeout: a short command completes normally.
    req = _FakeRequest(disconnected=False)
    result = asyncio.run(_exec_shell("echo ok", timeout=0, request=req))
    assert result["exit_code"] == 0, result
    assert "ok" in result["stdout"]
