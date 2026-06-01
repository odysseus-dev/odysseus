"""Tests for update_routes.py helpers."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from routes.update_routes import _require_admin, _generate_pull


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_request(user=None, is_admin=False, has_auth_manager=True):
    """Return a minimal fake Request for _require_admin tests."""
    auth_manager = None
    if has_auth_manager:
        auth_manager = MagicMock()
        auth_manager.is_admin.return_value = is_admin

    app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
    state = SimpleNamespace(current_user=user)
    return SimpleNamespace(app=app, state=state)


class _FakeStream:
    """Async readline()-able stream backed by a list of line strings."""

    def __init__(self, lines):
        self._lines = [line.encode() + b"\n" for line in lines]
        self._idx = 0

    async def readline(self):
        if self._idx < len(self._lines):
            data = self._lines[self._idx]
            self._idx += 1
            return data
        return b""


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess."""

    def __init__(self, stdout_lines, stderr_lines=(), returncode=0):
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode

    async def wait(self):
        pass


def _fake_disconnected():
    """Coroutine-compatible disconnect check that always returns False."""
    async def _check():
        return False
    return _check


# ── _require_admin ────────────────────────────────────────────────────────────


def _assert_admin_rejected(req):
    """Call _require_admin and assert it raises, checking status_code when available.

    The test suite stubs `fastapi` when it is not installed, which means
    `HTTPException` may be a MagicMock rather than a real exception class.
    We therefore catch any Exception and check for the 403 attribute only
    when the real HTTPException is present.
    """
    raised = None
    try:
        _require_admin(req)
    except Exception as e:
        raised = e
    assert raised is not None, "_require_admin should raise for this caller"
    if hasattr(raised, "status_code"):
        assert raised.status_code == 403


class TestRequireAdmin:
    def test_no_auth_manager_allows_any_caller(self):
        req = _make_request(user=None, has_auth_manager=False)
        assert _require_admin(req) is None

    def test_internal_tool_always_passes(self):
        req = _make_request(user="internal-tool")
        assert _require_admin(req) is None

    def test_unauthenticated_caller_rejected(self):
        _assert_admin_rejected(_make_request(user=None))

    def test_api_token_caller_rejected(self):
        _assert_admin_rejected(_make_request(user="api"))

    def test_non_admin_user_rejected(self):
        _assert_admin_rejected(_make_request(user="alice", is_admin=False))

    def test_admin_user_passes(self):
        req = _make_request(user="alice", is_admin=True)
        assert _require_admin(req) is None


# ── _generate_pull ────────────────────────────────────────────────────────────


async def _collect_pull(proc, tmp_trigger):
    """Drive _generate_pull with a fake subprocess and return parsed SSE events."""
    request = SimpleNamespace(is_disconnected=_fake_disconnected())

    async def _fake_exec(*args, stdout, stderr, cwd):
        return proc

    events = []
    import routes.update_routes as update_routes
    with patch.object(update_routes, "_TRIGGER_FILE", tmp_trigger), \
         patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        async for chunk in _generate_pull(request):
            line = chunk.strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    return events


class TestGeneratePull:
    @pytest.mark.asyncio
    async def test_streams_stdout_lines_as_sse(self, tmp_path):
        proc = _FakeProc(stdout_lines=["Already up to date."], returncode=0)
        events = await _collect_pull(proc, tmp_path / "update_ready")
        data_events = [e for e in events if "data" in e]
        assert any("Already up to date." in e["data"] for e in data_events)

    @pytest.mark.asyncio
    async def test_streams_stderr_lines_as_sse(self, tmp_path):
        proc = _FakeProc(stdout_lines=[], stderr_lines=["fatal: not a git repo"], returncode=128)
        events = await _collect_pull(proc, tmp_path / "update_ready")
        stderr_events = [e for e in events if e.get("stream") == "stderr"]
        assert any("fatal" in e["data"] for e in stderr_events)

    @pytest.mark.asyncio
    async def test_final_event_carries_exit_code(self, tmp_path):
        proc = _FakeProc(stdout_lines=["ok"], returncode=0)
        events = await _collect_pull(proc, tmp_path / "update_ready")
        exit_events = [e for e in events if "exit_code" in e]
        assert exit_events, "expected at least one exit_code event"
        assert exit_events[-1]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_propagated(self, tmp_path):
        proc = _FakeProc(stdout_lines=[], returncode=1)
        events = await _collect_pull(proc, tmp_path / "update_ready")
        exit_events = [e for e in events if "exit_code" in e]
        assert exit_events[-1]["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_trigger_file_written_on_success(self, tmp_path):
        trigger = tmp_path / "update_ready"
        proc = _FakeProc(stdout_lines=["Updating abc..def"], returncode=0)
        await _collect_pull(proc, trigger)
        assert trigger.exists(), "trigger file must be written after a clean pull"

    @pytest.mark.asyncio
    async def test_trigger_file_not_written_on_failure(self, tmp_path):
        trigger = tmp_path / "update_ready"
        proc = _FakeProc(stdout_lines=[], returncode=1)
        await _collect_pull(proc, trigger)
        assert not trigger.exists(), "trigger file must not be written when pull fails"


# ── setup_update_routes ───────────────────────────────────────────────────────


def test_setup_update_routes_returns_without_error():
    """setup_update_routes must be callable and return a non-None router object.

    Deep route inspection requires a real APIRouter, which is unavailable when
    fastapi is stubbed in the test environment. We verify the factory runs and
    returns something — the endpoint paths are covered by integration tests.
    """
    from routes.update_routes import setup_update_routes
    router = setup_update_routes()
    assert router is not None
