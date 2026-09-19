"""Regression tests for issue #2338 agent subprocess cleanup."""
import asyncio
import os
import shlex
import shutil
import signal
import sys
import tempfile

import pytest

from src.agent_tools.subprocess_tools import (
    _create_bash_subprocess,
    _kill_proc_tree,
    _posix_descendant_pids,
    _run_subprocess_streaming,
    _run_tmux_bash,
    _run_exec,
    _tmux_send_line,
    _tmux_session_name,
)


def _marker_path():
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".alive")
    path = handle.name
    handle.close()
    os.unlink(path)
    return path


async def _cancel_and_check(script: str, marker: str) -> bool:
    proc = await _create_bash_subprocess(
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if sys.platform != "win32":
        assert os.getpgid(proc.pid) == proc.pid
    task = asyncio.create_task(_run_subprocess_streaming(proc, timeout=30))
    for _ in range(30):
        if os.path.exists(marker):
            break
        await asyncio.sleep(0.05)
    assert os.path.exists(marker), "descendant never started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    if os.path.exists(marker):
        os.unlink(marker)
    await asyncio.sleep(0.7)
    survived = os.path.exists(marker)
    if survived:
        os.unlink(marker)
    return survived


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_cancel_kills_backgrounded_grandchild():
    marker = _marker_path()
    script = f"while :; do touch {shlex.quote(marker)}; sleep .1; done & wait"
    assert asyncio.run(_cancel_and_check(script, marker)) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_parent_exit_does_not_orphan_backgrounded_child():
    async def _run():
        marker = _marker_path()
        script = f"while :; do touch {shlex.quote(marker)}; sleep .1; done &"
        proc = await _create_bash_subprocess(
            script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, _, rc, timed_out = await _run_subprocess_streaming(proc, timeout=30)
        assert (rc, timed_out) == (0, False)
        if os.path.exists(marker):
            os.unlink(marker)
        await asyncio.sleep(0.7)
        survived = os.path.exists(marker)
        if survived:
            os.unlink(marker)
        assert survived is False

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_parent_exit_cleans_child_with_redirected_output():
    async def _run():
        marker = _marker_path()
        script = (
            f"(sleep .4; touch {shlex.quote(marker)}) "
            ">/dev/null 2>&1 &"
        )
        proc = await _create_bash_subprocess(
            script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, _, rc, timed_out = await _run_subprocess_streaming(proc, timeout=30)
        assert (rc, timed_out) == (0, False)
        await asyncio.sleep(0.7)
        survived = os.path.exists(marker)
        if survived:
            os.unlink(marker)
        assert survived is False

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
@pytest.mark.skipif(shutil.which("setsid") is None, reason="setsid unavailable")
def test_parent_exit_cleans_detached_child_with_redirected_output():
    async def _run():
        marker = _marker_path()
        inner = f"while :; do touch {shlex.quote(marker)}; sleep .1; done"
        script = (
            f"setsid sh -c {shlex.quote(inner)} >/dev/null 2>&1 & "
            "sleep .2"
        )
        proc = await _create_bash_subprocess(
            script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            for _ in range(30):
                if os.path.exists(marker):
                    break
                await asyncio.sleep(0.05)
            assert os.path.exists(marker), "detached child never started"
            _, _, rc, timed_out = await _run_subprocess_streaming(proc, timeout=30)
            assert (rc, timed_out) == (0, False)
            os.unlink(marker)
            await asyncio.sleep(0.7)
            assert not os.path.exists(marker)
        finally:
            _kill_proc_tree(proc)
            if os.path.exists(marker):
                os.unlink(marker)

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
@pytest.mark.skipif(shutil.which("setsid") is None, reason="setsid unavailable")
def test_cancel_kills_descendant_that_detaches_session():
    marker = _marker_path()
    inner = f"while :; do touch {shlex.quote(marker)}; sleep .1; done"
    script = f"setsid sh -c {shlex.quote(inner)} & wait"
    assert asyncio.run(_cancel_and_check(script, marker)) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_timeout_kills_backgrounded_grandchild():
    async def _run():
        marker = _marker_path()
        script = f"while :; do touch {shlex.quote(marker)}; sleep .1; done & wait"
        proc = await _create_bash_subprocess(
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, _, _, timed_out = await _run_subprocess_streaming(proc, timeout=0.5)
        if os.path.exists(marker):
            os.unlink(marker)
        await asyncio.sleep(0.7)
        survived = os.path.exists(marker)
        if survived:
            os.unlink(marker)
        return timed_out, survived

    assert asyncio.run(_run()) == (True, False)


def test_kill_proc_tree_tolerates_missing_pid():
    class NoPid:
        pid = None
        def kill(self):
            raise AssertionError("kill must not be called")
    _kill_proc_tree(NoPid())


@pytest.mark.skipif(sys.platform == "win32", reason="tmux is POSIX-only")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_cancel_kills_tmux_backgrounded_command(tmp_path):
    async def _run():
        marker = str(tmp_path / "tmux.alive")
        session_id = f"kill-tree-{os.getpid()}"
        script = f"while :; do touch {shlex.quote(marker)}; sleep .1; done & wait"
        task = asyncio.create_task(
            _run_tmux_bash(
                script,
                session_id=session_id,
                cwd=str(tmp_path),
                env=None,
                timeout=30,
            )
        )
        try:
            for _ in range(60):
                if os.path.exists(marker):
                    break
                await asyncio.sleep(0.05)
            assert os.path.exists(marker), "tmux descendant never started"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            os.unlink(marker)
            await asyncio.sleep(0.7)
            assert not os.path.exists(marker)
            reuse_marker = str(tmp_path / "session-reused")
            _, _, rc, timed_out = await _run_tmux_bash(
                f"touch {shlex.quote(reuse_marker)}",
                session_id=session_id,
                cwd=str(tmp_path),
                env=None,
                timeout=3,
            )
            assert (rc, timed_out) == (0, False)
            assert os.path.exists(reuse_marker)
        finally:
            process = await asyncio.create_subprocess_exec(
                "tmux", "kill-session", "-t", f"ody-agent-{session_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            if os.path.exists(marker):
                os.unlink(marker)

    asyncio.run(_run())


def test_tmux_session_names_do_not_collide_after_sanitizing():
    assert _tmux_session_name("chat/one") != _tmux_session_name("chat-one")


def test_tmux_session_names_do_not_collide_after_truncating():
    prefix = "a" * 80
    first = _tmux_session_name(prefix + "-first")
    second = _tmux_session_name(prefix + "-second")

    assert first != second
    assert len(first) <= len("ody-agent-") + 80


@pytest.mark.skipif(sys.platform == "win32", reason="tmux is POSIX-only")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_tmux_session_names_do_not_collide_after_tmux_canonicalizing_periods():
    async def _run():
        names = [_tmux_session_name("chat.one"), _tmux_session_name("chat_one")]
        assert names[0] != names[1]
        try:
            for name in names:
                _, stderr, rc = await _run_exec(
                    "tmux", "new-session", "-d", "-s", name, timeout=3
                )
                assert rc == 0, stderr
            assert all([await _tmux_has_session_for_test(name) for name in names])
        finally:
            for name in names:
                await _run_exec("tmux", "kill-session", "-t", name, timeout=3)

    async def _tmux_has_session_for_test(name):
        _, _, rc = await _run_exec("tmux", "has-session", "-t", name, timeout=3)
        return rc == 0

    asyncio.run(_run())


def test_run_exec_timeout_reaps_helper_process(monkeypatch):
    class FakeProcess:
        returncode = None

        def __init__(self):
            self.killed = False
            self.communicated_after_kill = False

        async def communicate(self):
            if not self.killed:
                await asyncio.Future()
            self.communicated_after_kill = True
            self.returncode = -signal.SIGKILL
            return b"", b""

        def kill(self):
            self.killed = True

    async def _run():
        proc = FakeProcess()

        async def create_subprocess_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
        result = await _run_exec("tmux", "has-session", timeout=0.001)
        assert result == ("", "timeout", 124)
        assert proc.killed is True
        assert proc.communicated_after_kill is True

    asyncio.run(_run())


def test_run_exec_timeout_drains_full_output_pipe():
    async def _run():
        result = await asyncio.wait_for(
            _run_exec(
                sys.executable,
                "-c",
                "import sys, time; sys.stdout.write('x' * 10_000_000); "
                "sys.stdout.flush(); time.sleep(10)",
                timeout=0.01,
            ),
            timeout=3,
        )
        assert result == ("", "timeout", 124)

    asyncio.run(_run())


def test_tmux_send_line_submits_text_and_enter_atomically(monkeypatch):
    async def _run():
        calls = []

        async def run_exec(*args, **kwargs):
            calls.append((args, kwargs))
            return "", "", 0

        monkeypatch.setattr("src.agent_tools.subprocess_tools._run_exec", run_exec)
        await _tmux_send_line("session", "printf ok")
        assert calls == [(
            (
                "tmux", "send-keys", "-t", "session", "-l", "printf ok",
                ";", "send-keys", "-t", "session", "C-m",
            ),
            {"timeout": 5},
        )]

    asyncio.run(_run())



@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="procfs behavior")
def test_posix_parent_map_closes_proc_stat_files(monkeypatch):
    import io

    from src.agent_tools.subprocess_tools import _posix_parent_map

    opened = []

    class Entry:
        name = "123"

    class TrackedStat(io.StringIO):
        def close(self):
            opened.append("closed")
            super().close()

    monkeypatch.setattr("src.agent_tools.subprocess_tools.os.scandir", lambda path: [Entry()])
    handles = []

    def tracked_open(*args, **kwargs):
        handle = TrackedStat("123 (cmd) S 1 0 0 0")
        handles.append(handle)
        return handle

    monkeypatch.setattr("builtins.open", tracked_open)

    assert _posix_parent_map() == {1: [123]}
    assert opened == ["closed"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="procfs behavior")
def test_linux_child_pids_reads_children_from_every_thread(monkeypatch):
    import io

    from src.agent_tools.subprocess_tools import _linux_child_pids

    class Entry:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(
        "src.agent_tools.subprocess_tools.os.scandir",
        lambda path: [Entry("100"), Entry("101"), Entry("metadata")],
    )

    def fake_open(path, **kwargs):
        if "/task/100/children" in path:
            return io.StringIO("200")
        if "/task/101/children" in path:
            return io.StringIO("201 200")
        raise OSError(path)

    monkeypatch.setattr("builtins.open", fake_open)

    assert _linux_child_pids(100) == [200, 201]


def test_linux_child_pids_closes_proc_children_file(monkeypatch):
    import io

    from src.agent_tools.subprocess_tools import _linux_child_pids

    handles = []

    class TrackedChildren(io.StringIO):
        pass

    def tracked_open(*args, **kwargs):
        handle = TrackedChildren("11 12")
        handles.append(handle)
        return handle

    monkeypatch.setattr("builtins.open", tracked_open)

    assert _linux_child_pids(10) == [11, 12]
    assert handles[0].closed


def test_posix_descendant_walk_handles_cycles(monkeypatch):
    if sys.platform.startswith("linux"):
        monkeypatch.setattr(
            "src.agent_tools.subprocess_tools._linux_child_pids",
            lambda pid: {10: [11], 11: [12], 12: [11]}.get(pid, []),
        )
    else:
        monkeypatch.setattr(
            "src.agent_tools.subprocess_tools._posix_parent_map",
            lambda: {10: [11], 11: [12], 12: [11]},
        )
    assert _posix_descendant_pids(10) == [11, 12]


def test_windows_kill_dispatch(monkeypatch):
    called = []
    monkeypatch.setattr("src.agent_tools.subprocess_tools.IS_WINDOWS", True)
    monkeypatch.setattr(
        "src.agent_tools.subprocess_tools.kill_process_tree", called.append
    )

    class Proc:
        pid = 123

    _kill_proc_tree(Proc())
    assert called == [123]


def test_cancel_while_draining_exited_process_still_requests_tree_cleanup(monkeypatch):
    async def _run():
        reader_started = asyncio.Event()
        cleanup_calls = []

        class Stream:
            async def readline(self):
                reader_started.set()
                await asyncio.Event().wait()

        class Proc:
            pid = 123
            returncode = 0
            stdout = Stream()
            stderr = Stream()

        monkeypatch.setattr(
            "src.agent_tools.subprocess_tools._kill_proc_tree", cleanup_calls.append
        )
        monkeypatch.setattr(
            "src.agent_tools.subprocess_tools._kill_remembered_proc_group",
            lambda proc, descendants=None: None,
        )

        task = asyncio.create_task(_run_subprocess_streaming(Proc(), timeout=30))
        await reader_started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(cleanup_calls) == 1
        assert cleanup_calls[0].pid == 123

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="tmux is POSIX-only")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_timeout_kills_tmux_backgrounded_command(tmp_path):
    async def _run():
        marker = str(tmp_path / "tmux-timeout.alive")
        session_id = f"kill-tree-timeout-{os.getpid()}"
        script = f"while :; do touch {shlex.quote(marker)}; sleep .1; done & wait"
        try:
            _, _, rc, timed_out = await _run_tmux_bash(
                script,
                session_id=session_id,
                cwd=str(tmp_path),
                env=None,
                timeout=1,
            )
            assert (rc, timed_out) == (124, True)
            assert os.path.exists(marker), "tmux descendant never started"
            os.unlink(marker)
            await asyncio.sleep(0.7)
            assert not os.path.exists(marker)
        finally:
            process = await asyncio.create_subprocess_exec(
                "tmux", "kill-session", "-t", f"ody-agent-{session_id}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            if os.path.exists(marker):
                os.unlink(marker)

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="tmux is POSIX-only")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_timeout_recovers_tmux_after_command_exec_replaces_shell(tmp_path):
    async def run():
        session_id = f"exec-replaces-shell-{os.getpid()}"
        name = f"ody-agent-{session_id}"
        workdir = tmp_path / "changed-cwd"
        workdir.mkdir()
        marker = workdir / "recovered"
        try:
            _, _, rc, timed_out = await _run_tmux_bash(
                f"cd {workdir}",
                session_id=session_id, cwd=str(tmp_path), env=None, timeout=3,
            )
            assert (rc, timed_out) == (0, False)

            _, _, rc, timed_out = await _run_tmux_bash(
                "exec python3 -c 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(30)'",
                session_id=session_id, cwd=str(tmp_path), env=None, timeout=0.5,
            )
            assert (rc, timed_out) == (124, True)

            _, _, rc, timed_out = await _run_tmux_bash(
                "touch recovered",
                session_id=session_id, cwd=str(tmp_path), env=None, timeout=3,
            )
            assert (rc, timed_out) == (0, False)
            assert marker.exists()
        finally:
            await _run_exec("tmux", "kill-session", "-t", name, timeout=3)

    asyncio.run(run())


@pytest.mark.skipif(sys.platform == "win32", reason="tmux is POSIX-only")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_cancel_preserves_preexisting_tmux_background_job(tmp_path):
    async def _run():
        survivor = str(tmp_path / "survivor.alive")
        cancelled = str(tmp_path / "cancelled.alive")
        session_id = f"kill-tree-preserve-{os.getpid()}"
        try:
            _, _, rc, timed_out = await _run_tmux_bash(
                f"(while :; do touch {shlex.quote(survivor)}; sleep .1; done) &",
                session_id=session_id, cwd=str(tmp_path), env=None, timeout=3,
            )
            assert (rc, timed_out) == (0, False)
            for _ in range(30):
                if os.path.exists(survivor):
                    break
                await asyncio.sleep(0.05)
            assert os.path.exists(survivor)

            script = f"while :; do touch {shlex.quote(cancelled)}; sleep .1; done & wait"
            task = asyncio.create_task(_run_tmux_bash(
                script, session_id=session_id, cwd=str(tmp_path), env=None, timeout=30,
            ))
            for _ in range(60):
                if os.path.exists(cancelled):
                    break
                await asyncio.sleep(0.05)
            assert os.path.exists(cancelled)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            os.unlink(survivor)
            os.unlink(cancelled)
            await asyncio.sleep(0.7)
            assert os.path.exists(survivor), "cancellation killed an older session job"
            assert not os.path.exists(cancelled), "cancelled command survived"
        finally:
            process = await asyncio.create_subprocess_exec(
                "tmux", "kill-session", "-t", f"ody-agent-{session_id}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await process.wait()
            for marker in (survivor, cancelled):
                if os.path.exists(marker):
                    os.unlink(marker)

    asyncio.run(_run())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
def test_creation_cancel_remembers_group_after_leader_exits(monkeypatch, tmp_path):
    async def _run():
        marker = str(tmp_path / "creation-cancel.alive")
        started = asyncio.Event()
        release = asyncio.Event()
        original_create = asyncio.create_subprocess_shell
        pgid = None

        async def delayed_create(command, **kwargs):
            nonlocal pgid
            proc = await original_create(command, **kwargs)
            pgid = proc.pid
            await proc.wait()
            started.set()
            await release.wait()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_shell", delayed_create)
        script = (
            f"(while :; do touch {shlex.quote(marker)}; sleep .1; done) "
            ">/dev/null 2>&1 &"
        )
        task = asyncio.create_task(_create_bash_subprocess(
            script, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            for _ in range(30):
                if os.path.exists(marker):
                    break
                await asyncio.sleep(0.05)
            assert os.path.exists(marker), "background child never started"
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            os.unlink(marker)
            await asyncio.sleep(0.7)
            assert not os.path.exists(marker), (
                "creation cancellation lost the group after its leader exited"
            )
        finally:
            release.set()
            if not task.done():
                task.cancel()
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if os.path.exists(marker):
                os.unlink(marker)

    asyncio.run(_run())


def test_cancel_during_bash_process_creation_cleans_created_process(monkeypatch):
    async def _run():
        started = asyncio.Event()
        release = asyncio.Event()

        class Proc:
            pid = 4321
            returncode = None
            killed = False
            def kill(self):
                self.killed = True
            async def wait(self):
                self.returncode = -9

        proc = Proc()

        async def delayed_create(command, **kwargs):
            started.set()
            await release.wait()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_shell", delayed_create)
        monkeypatch.setattr(
            "src.agent_tools.subprocess_tools._kill_proc_tree", lambda child: child.kill()
        )
        task = asyncio.create_task(_create_bash_subprocess("sleep 60"))
        await started.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert proc.killed
        assert proc.returncode == -9

    asyncio.run(_run())


def test_cancel_during_existing_tmux_setup_does_not_interrupt(monkeypatch, tmp_path):
    async def _run():
        setup_started = asyncio.Event()
        release_setup = asyncio.Event()
        interrupted = False

        async def has_session(name):
            return True

        async def run_exec(*args, **kwargs):
            if args[:2] == ("tmux", "display-message"):
                return "123", "", 0
            return "", "", 0

        async def ensure_session(name, cwd, env):
            setup_started.set()
            await release_setup.wait()

        async def interrupt(name, protected_descendants=None):
            nonlocal interrupted
            interrupted = True

        monkeypatch.setattr("src.agent_tools.subprocess_tools._tmux_has_session", has_session)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._run_exec", run_exec)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._ensure_tmux_session", ensure_session)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._interrupt_tmux_command", interrupt)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._posix_descendant_pids", lambda pid: [124])

        task = asyncio.create_task(_run_tmux_bash(
            "echo never-sent", session_id="existing", cwd=str(tmp_path),
            env=None, timeout=30,
        ))
        await setup_started.wait()
        task.cancel()
        release_setup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not interrupted

    asyncio.run(_run())


def test_cancel_before_first_tmux_line_does_not_interrupt(monkeypatch, tmp_path):
    async def _run():
        send_started = asyncio.Event()
        release_send = asyncio.Event()
        interrupted = False

        async def has_session(name): return True
        async def run_exec(*args, **kwargs):
            if args[:2] == ("tmux", "display-message"):
                return "123", "", 0
            return "", "", 0
        async def ensure_session(name, cwd, env): return None
        async def send_line(name, line):
            send_started.set()
            await release_send.wait()
        async def interrupt(name, protected_descendants=None):
            nonlocal interrupted
            interrupted = True

        monkeypatch.setattr("src.agent_tools.subprocess_tools._tmux_has_session", has_session)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._run_exec", run_exec)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._ensure_tmux_session", ensure_session)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._tmux_send_line", send_line)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._interrupt_tmux_command", interrupt)
        monkeypatch.setattr("src.agent_tools.subprocess_tools._posix_descendant_pids", lambda pid: [])
        task = asyncio.create_task(_run_tmux_bash(
            "echo never-sent", session_id="existing-first-line",
            cwd=str(tmp_path), env=None, timeout=30,
        ))
        await send_started.wait()
        task.cancel()
        release_send.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not interrupted

    asyncio.run(_run())


def test_kill_proc_tree_does_not_signal_reused_root_group(monkeypatch):
    """A stale Process handle must not kill a group whose leader PID was reused."""
    from types import SimpleNamespace
    from src.agent_tools import subprocess_tools as tools

    proc = SimpleNamespace(
        pid=4100,
        returncode=0,
        _odysseus_pgid=4100,
        _odysseus_identity=(4100, 11),
        kill=lambda: (_ for _ in ()).throw(AssertionError("stale pid killed")),
    )
    monkeypatch.setattr(tools, "IS_WINDOWS", False)
    monkeypatch.setattr(tools, "_posix_descendant_pids", lambda _pid: [])
    monkeypatch.setattr(tools.os, "getpgrp", lambda: 999)
    monkeypatch.setattr(
        tools, "_posix_process_identity",
        lambda pid: (4100, 22) if pid == 4100 else None,
    )
    signalled = []
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: signalled.append(pgid))

    tools._kill_proc_tree(proc)

    assert signalled == []


def test_interrupt_tmux_ignores_reused_protected_pid(monkeypatch):
    """A reused PID must not protect a new command descendant from cleanup."""
    from src.agent_tools import subprocess_tools as tools

    async def run_exec(*args, **kwargs):
        if args[:2] == ("tmux", "display-message"):
            return "100\n", "", 0
        return "", "", 0

    async def send_line(*args, **kwargs):
        return None

    async def capture(*args, **kwargs):
        return "__ODYSSEUS_INTERRUPT_READY_"

    monkeypatch.setattr(tools, "_run_exec", run_exec)
    monkeypatch.setattr(tools, "_tmux_send_line", send_line)
    monkeypatch.setattr(tools, "_tmux_capture", capture)
    monkeypatch.setattr(tools, "_posix_descendant_pids", lambda pid: [200] if pid == 100 else [])
    monkeypatch.setattr(
        tools, "_posix_process_identity",
        lambda pid: (200, 99) if pid == 200 else (100, 1),
    )
    monkeypatch.setattr(tools.os, "getpgid", lambda pid: pid)
    killed_pids = []
    killed_groups = []
    monkeypatch.setattr(tools.os, "kill", lambda pid, sig: killed_pids.append(pid))
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: killed_groups.append(pgid))

    asyncio.run(tools._interrupt_tmux_command("s", {200: (200, 12)}))

    assert killed_pids == [200]
    assert killed_groups == [200]


def test_interrupt_tmux_cleans_descendant_spawned_by_sigint_trap(monkeypatch):
    """Rescan after C-c so an INT trap cannot fork a cleanup escapee."""
    from src.agent_tools import subprocess_tools as tools

    interrupted = False

    async def run_exec(*args, **kwargs):
        nonlocal interrupted
        if args[:2] == ("tmux", "display-message"):
            return "100\n", "", 0
        if "C-c" in args:
            interrupted = True
        return "", "", 0

    async def noop(*args, **kwargs):
        return None

    async def capture(*args, **kwargs):
        return "__ODYSSEUS_INTERRUPT_READY_"

    monkeypatch.setattr(tools, "_run_exec", run_exec)
    monkeypatch.setattr(tools, "_tmux_send_line", noop)
    monkeypatch.setattr(tools, "_tmux_capture", capture)
    monkeypatch.setattr(
        tools, "_posix_descendant_pids",
        lambda pid: ([200, 201] if interrupted else [200]) if pid == 100 else [],
    )
    monkeypatch.setattr(tools, "_posix_process_identity", lambda pid: (pid, pid + 1))
    monkeypatch.setattr(tools.os, "getpgid", lambda pid: pid)
    killed_pids = []
    killed_groups = []
    monkeypatch.setattr(tools.os, "kill", lambda pid, sig: killed_pids.append(pid))
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: killed_groups.append(pgid))

    asyncio.run(tools._interrupt_tmux_command("s"))

    assert 201 in killed_pids
    assert 201 in killed_groups


def test_interrupt_tmux_preserves_child_forked_by_protected_job(monkeypatch):
    """A prior persistent job's child must remain protected after C-c."""
    from src.agent_tools import subprocess_tools as tools

    interrupted = False

    async def run_exec(*args, **kwargs):
        nonlocal interrupted
        if args[:2] == ("tmux", "display-message"):
            return "100\n", "", 0
        if "C-c" in args:
            interrupted = True
        return "", "", 0

    async def noop(*args, **kwargs): return None
    async def capture(*args, **kwargs): return "__ODYSSEUS_INTERRUPT_READY_"

    def descendants(pid):
        if pid == 100:
            return [200, 201] if interrupted else [200]
        if pid == 200 and interrupted:
            return [201]
        return []

    monkeypatch.setattr(tools, "_run_exec", run_exec)
    monkeypatch.setattr(tools, "_tmux_send_line", noop)
    monkeypatch.setattr(tools, "_tmux_capture", capture)
    monkeypatch.setattr(tools, "_posix_descendant_pids", descendants)
    monkeypatch.setattr(tools, "_posix_process_identity", lambda pid: (pid, pid + 1))
    monkeypatch.setattr(tools, "_pid_is_confirmed_absent", lambda pid: False)
    monkeypatch.setattr(tools.os, "getpgid", lambda pid: pid)
    killed = []
    monkeypatch.setattr(tools.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: killed.append(pgid))

    asyncio.run(tools._interrupt_tmux_command("s", {200: (200, 201)}))

    assert killed == []


def test_interrupt_tmux_preserves_portable_live_protected_pid(monkeypatch):
    """POSIX platforms without start identities still preserve live old jobs."""
    from src.agent_tools import subprocess_tools as tools

    async def run_exec(*args, **kwargs):
        if args[:2] == ("tmux", "display-message"):
            return "100\n", "", 0
        return "", "", 0
    async def noop(*args, **kwargs):
        return None

    async def capture(*args, **kwargs):
        return "__ODYSSEUS_INTERRUPT_READY_"

    monkeypatch.setattr(tools, "_run_exec", run_exec)
    monkeypatch.setattr(tools, "_tmux_send_line", noop)
    monkeypatch.setattr(tools, "_tmux_capture", capture)
    monkeypatch.setattr(tools, "_posix_descendant_pids", lambda pid: [200] if pid == 100 else [])
    monkeypatch.setattr(tools, "_posix_process_identity", lambda pid: None)
    monkeypatch.setattr(tools, "_pid_is_confirmed_absent", lambda pid: False)
    monkeypatch.setattr(tools.os, "getpgid", lambda pid: pid)
    killed = []
    monkeypatch.setattr(tools.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(tools.os, "killpg", lambda pgid, sig: killed.append(pgid))

    asyncio.run(tools._interrupt_tmux_command("s", {200: None}))

    assert killed == []
