"""Regression tests for issue #2338 agent subprocess cleanup."""
import asyncio
import os
import shlex
import shutil
import sys
import tempfile

import pytest

from src.agent_tools.subprocess_tools import (
    _create_bash_subprocess,
    _kill_proc_tree,
    _posix_descendant_pids,
    _run_subprocess_streaming,
    _run_tmux_bash,
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


def test_posix_descendant_walk_handles_cycles(monkeypatch):
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
