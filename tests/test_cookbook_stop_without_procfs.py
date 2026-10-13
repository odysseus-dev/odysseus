"""Cookbook selectors and OS observations never mint application authority.

These legacy UI-backed targets have no authoritative launch registry. Local
agent stops therefore fail closed before discovery, signalling or state writes,
on both procfs and other hosts. Shared Wave 5B lifecycle mechanics are tested
separately in test_process_lifecycle and test_process_ownership.
"""
import asyncio
import json
import os
import signal

import pytest

from core import platform_compat
from src import tool_implementations as tools


class FakeResponse:
    def __init__(self, data=None, status_code=200):
        self._data = data or {}
        self.status_code = status_code
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


def _tracked_state(session_id="serve-abc123", cmd="python -m vllm.entrypoints.openai.api_server"):
    return {
        "tasks": [
            {
                "sessionId": session_id,
                "model": "org/model",
                "type": "serve",
                "status": "running",
                "payload": {"_cmd": cmd},
            }
        ]
    }


def _install_httpx_client(monkeypatch, state):
    """Serve cookbook state over a fake httpx and record every POST body."""
    import httpx

    posts = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse(state)

        async def post(self, url, json=None, **kwargs):
            posts.append((url, json))
            return FakeResponse({"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return posts


def _install_successful_tmux_kill(monkeypatch, panes=""):
    """Fake the two tmux calls a stop makes: list-panes, then kill-session.

    ``panes`` is the ``list-panes`` stdout, i.e. ``"<session> <pane_pid>"`` per
    line — the stop reads it to learn which processes the session owns before
    the kill destroys that link.
    """
    calls = []

    class FakeProc:
        returncode = 0

        def __init__(self, stdout=b""):
            self._stdout = stdout

        async def communicate(self):
            return self._stdout, b""

    async def fake_exec(*argv, **kwargs):
        calls.append(argv)
        assert argv[0] == "tmux"
        if argv[1] == "list-panes":
            return FakeProc(panes.encode())
        assert argv[1] == "kill-session"
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


def _stopped_statuses(posts, session_id):
    out = []
    for _url, body in posts:
        for task in (body or {}).get("tasks") or []:
            if task.get("sessionId") == session_id:
                out.append(task.get("status"))
    return out


def _fake_table(monkeypatch, rows):
    """Substitute the process table. ``rows`` is {pid: (ppid, command)}.

    Returns the live dict, so a test can model a process actually dying by
    removing it: ``start_token`` reads from the same dict, and a pid that is no
    longer in it has no token, which :func:`process_ownership.verify` reports as
    ``GONE``.
    """
    from src import process_ownership

    table = {
        pid: process_ownership.ProcessInfo(pid=pid, ppid=ppid, command=command)
        for pid, (ppid, command) in rows.items()
    }
    monkeypatch.setattr(process_ownership, "process_table", lambda: dict(table))
    # Identity is what authorises a signal, so every pid in the fake table has
    # one. A pid absent from the table has no token and cannot be signalled.
    monkeypatch.setattr(
        process_ownership, "start_token",
        lambda pid: f"token:{pid}" if int(pid or 0) in table else None,
    )
    return table


def _install_effective_kill(monkeypatch, table):
    """Record signals, and let SIGTERM actually remove the process.

    Keeps the sweep off its escalation path, which would otherwise spend the
    full SIGTERM grace plus the SIGKILL confirmation window on every pid.
    """
    signalled = []

    def _kill(pid, sig):
        signalled.append((pid, sig))
        table.pop(int(pid), None)

    monkeypatch.setattr(os, "kill", _kill)
    return signalled


@pytest.mark.asyncio
async def test_unadmitted_stop_refused_when_the_host_has_no_procfs(
    monkeypatch, tmp_path
):
    """The ODY-94 regression: no procfs must not turn a working stop into a failure."""
    state = _tracked_state()
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")
    # ps is the mechanism on a procfs-less host; the sweep goes through it
    # instead of being skipped.
    _fake_table(monkeypatch, {900: (1, "bash")})

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["failure_kind"] == "resource_identity_denied"
    assert _stopped_statuses(posts, "serve-abc123") == []


@pytest.mark.asyncio
async def test_unadmitted_stop_refused_when_the_session_cannot_be_inspected(
    monkeypatch, tmp_path
):
    """A sweep that could not look must not read as a sweep that found nothing.

    This is the half of ODY-94 that the procfs guard left behind: skipping the
    sweep stopped the crash and still reported plain success.
    """
    from src import process_ownership

    state = _tracked_state()
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")

    def _no_inspection():
        raise process_ownership.InspectionUnavailable("the process table")

    monkeypatch.setattr(process_ownership, "process_table", _no_inspection)

    signalled = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["failure_kind"] == "resource_identity_denied"
    assert signalled == []
    assert _stopped_statuses(posts, "serve-abc123") == []


@pytest.mark.asyncio
async def test_pane_descendant_is_not_application_owned(monkeypatch, tmp_path):
    """A process under a named pane still requires prior application admission."""
    tracked_cmd = "python -m vllm.entrypoints.openai.api_server --model org/model"
    state = _tracked_state(cmd=tracked_cmd)
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    table = _fake_table(monkeypatch, {
        900: (1, "bash"),             # the pane shell
        101: (900, tracked_cmd),      # the model server it started — ours
    })
    signalled = _install_effective_kill(monkeypatch, table)

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["failure_kind"] == "resource_identity_denied"
    assert signalled == []  # OS lineage alone never establishes app ownership.
    assert _stopped_statuses(posts, "serve-abc123") == []


@pytest.mark.asyncio
async def test_unadmitted_stop_never_signals_a_command_line_lookalike(
    monkeypatch, tmp_path
):
    """The headline change: matching the command line is not owning the process.

    pid 202 runs exactly the tracked command but descends from nothing this
    session started — a server the user launched by hand looks precisely like
    this. The old sweep killed it.
    """
    tracked_cmd = "python -m vllm.entrypoints.openai.api_server --model org/model"
    state = _tracked_state(cmd=tracked_cmd)
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    table = _fake_table(monkeypatch, {
        900: (1, "bash"),
        202: (1, tracked_cmd),   # same command, different lineage
    })
    signalled = _install_effective_kill(monkeypatch, table)

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["failure_kind"] == "resource_identity_denied"
    assert not any(pid == 202 for pid, _sig in signalled)
    assert _stopped_statuses(posts, "serve-abc123") == []


@pytest.mark.asyncio
async def test_stop_does_not_signal_a_pid_whose_identity_changed(
    monkeypatch, tmp_path
):
    """Captured before the kill, recycled before the sweep: do not signal it."""
    from src import process_ownership

    tracked_cmd = "python -m vllm.entrypoints.openai.api_server --model org/model"
    state = _tracked_state(cmd=tracked_cmd)
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    table = _fake_table(monkeypatch, {900: (1, "bash"), 101: (900, tracked_cmd)})

    # pid 101's slot reads differently every time it is asked, so whatever the
    # capture recorded, the sweep's re-check cannot match it: the pid was
    # recycled in between. Every other pid keeps a stable identity.
    drift = {"n": 0}

    def _drifting_token(pid):
        if int(pid) == 101:
            drift["n"] += 1
            return f"token:101:{drift['n']}"
        return f"token:{pid}" if int(pid or 0) in table else None

    monkeypatch.setattr(process_ownership, "start_token", _drifting_token)
    signalled = _install_effective_kill(monkeypatch, table)

    result = await tools.do_stop_served_model(
        json.dumps({"session_id": "serve-abc123"})
    )

    assert result["failure_kind"] == "resource_identity_denied"
    # Neither pane discovery nor a matching token creates application scope.
    assert not any(pid == 101 for pid, _sig in signalled)
    assert _stopped_statuses(posts, "serve-abc123") == []


def test_model_process_scan_returns_empty_without_procfs(monkeypatch, tmp_path):
    """The other procfs scan in the same module already guards; pin it."""
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")

    import os

    def _unexpected_listdir(*args, **kwargs):
        raise AssertionError("the model-process scan must not run without procfs")

    monkeypatch.setattr(os, "listdir", _unexpected_listdir)

    assert tools._scan_running_model_processes() == []


@pytest.mark.asyncio
async def test_unadmitted_stop_refused_with_unverifiable_process(monkeypatch, tmp_path):
    """An unverifiable OS observation cannot create an application grant."""
    from src import process_ownership

    tracked_cmd = "python -m vllm.entrypoints.openai.api_server --model org/model"
    state = _tracked_state(cmd=tracked_cmd)
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    table = _fake_table(monkeypatch, {900: (1, "bash"), 101: (900, tracked_cmd)})
    asked = {"n": 0}

    def _token(pid):
        if int(pid) == 101:
            asked["n"] += 1
            if asked["n"] > 1:  # the capture succeeded; every later look fails
                raise process_ownership.InspectionUnavailable("/proc/101/stat")
            return "token:101"
        return f"token:{pid}" if int(pid or 0) in table else None

    monkeypatch.setattr(process_ownership, "start_token", _token)
    signalled = _install_effective_kill(monkeypatch, table)

    result = await tools.do_stop_served_model(json.dumps({"session_id": "serve-abc123"}))

    assert result["failure_kind"] == "resource_identity_denied"
    assert not any(pid == 101 for pid, _sig in signalled)
    assert _stopped_statuses(posts, "serve-abc123") == []


@pytest.mark.asyncio
async def test_stop_never_signals_a_pid_reissued_between_the_table_and_its_capture(
    monkeypatch, tmp_path
):
    """The table places 101 under the pane; 101 is then reissued to a stranger.

    The stranger's token must never be the one recorded for the session.
    """
    from src import process_ownership

    tracked_cmd = "python -m vllm.entrypoints.openai.api_server --model org/model"
    state = _tracked_state(cmd=tracked_cmd)
    posts = _install_httpx_client(monkeypatch, state)
    _install_successful_tmux_kill(monkeypatch, panes="serve-abc123 900\n")
    table = _fake_table(monkeypatch, {900: (1, "bash"), 101: (900, tracked_cmd)})
    reads = {"n": 0}

    def _table():
        reads["n"] += 1
        if reads["n"] > 1:
            # After the first read: the server exited and its pid now belongs
            # to an unrelated process with a fresh identity.
            table[101] = process_ownership.ProcessInfo(101, 1, "sshd: stranger")
        return dict(table)

    monkeypatch.setattr(process_ownership, "process_table", _table)
    signalled = _install_effective_kill(monkeypatch, table)

    result = await tools.do_stop_served_model(json.dumps({"session_id": "serve-abc123"}))

    assert result["failure_kind"] == "resource_identity_denied"
    assert not any(pid == 101 for pid, _sig in signalled)
    assert _stopped_statuses(posts, "serve-abc123") == []
