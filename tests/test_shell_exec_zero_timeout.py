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

import pytest

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


def test_external_cancellation_propagates():
    # A still-connected client never trips the disconnect poll, so the only way
    # out of a no-timeout command is external cancellation (request-timeout
    # middleware / shutdown). That must propagate as CancelledError — not be
    # swallowed into a normal shell result — after the subprocess is torn down,
    # and it must return promptly rather than stalling in cleanup.
    async def scenario():
        req = _FakeRequest(disconnected=False)
        task = asyncio.ensure_future(_exec_shell("sleep 30", timeout=0, request=req))
        await asyncio.sleep(0.3)  # let the subprocess spawn and enter the poll loop
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "cancelled"
        return "masked"

    result = asyncio.run(asyncio.wait_for(scenario(), timeout=8))
    assert result == "cancelled", "external cancellation must propagate, not be masked"


def test_shell_exec_exempt_from_request_hard_timeout():
    # A no-timeout buffered exec must not be cut off by the app-wide hard timeout
    # (the route now owns its subprocess timeout + disconnect cleanup), mirroring
    # the already-exempt /api/shell/stream. Skips locally when app deps are
    # absent; runs in CI where the full app imports.
    app_mod = pytest.importorskip("app")
    prefixes = app_mod._TIMEOUT_EXEMPT_PREFIXES
    assert any(
        "/api/shell/exec".startswith(p) for p in prefixes
    ), "/api/shell/exec must be exempt from REQUEST_HARD_TIMEOUT"
