"""Tier-B sandbox: end-to-end over a real Unix socket, no Docker required.

Launches docker/sandbox/runner.py as a local subprocess listening on a temp
socket, then drives it through src.sandbox_client. Proves the wire protocol,
execution, exit codes, timeout, secret isolation, and the client's
SandboxUnavailable behavior. The Docker-level isolation (network none, read-only
rootfs) is declarative in compose and checked manually (see scoping doc §5).
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from src.sandbox_client import run_in_sandbox, SandboxUnavailable

_RUNNER = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "docker", "sandbox", "runner.py")

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix-socket runner is POSIX-only")


@pytest.fixture()
def runner():
    # Bind under a SHORT dir: macOS caps AF_UNIX paths at ~104 chars, and pytest's
    # tmp_path is too long. /tmp keeps the socket path well under the limit.
    base = tempfile.mkdtemp(prefix="sbx", dir="/tmp")
    sock = os.path.join(base, "r.sock")
    work = os.path.join(base, "work")
    os.makedirs(work, exist_ok=True)
    env = {
        **os.environ,
        "RUNNER_SOCK": sock,
        "RUNNER_WORKDIR": work,
        # A secret in the RUNNER's env — the child must NOT inherit it.
        "FAKE_SECRET_TOKEN": "leak-me-not",
    }
    proc = subprocess.Popen([sys.executable, _RUNNER], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Wait for the socket to appear (runner binds on startup).
    for _ in range(100):
        if os.path.exists(sock):
            break
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"runner exited early: {out}")
        time.sleep(0.05)
    else:
        proc.terminate()
        raise RuntimeError("runner socket never appeared")
    yield sock
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(base, ignore_errors=True)


def _call(sock, tool, code, timeout=30):
    return asyncio.run(run_in_sandbox(tool, code, timeout=timeout, sock_path=sock))


def test_bash_echo(runner):
    res = _call(runner, "bash", "echo hello-sandbox")
    assert res["exit_code"] == 0
    assert "hello-sandbox" in res["stdout"]
    assert res["timed_out"] is False


def test_python_exec(runner):
    res = _call(runner, "python", "print(6*7)")
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "42"


def test_nonzero_exit_code(runner):
    res = _call(runner, "bash", "exit 3")
    assert res["exit_code"] == 3


def test_stderr_captured(runner):
    res = _call(runner, "bash", "echo oops 1>&2")
    assert "oops" in res["stderr"]


def test_timeout_kills(runner):
    res = _call(runner, "bash", "sleep 10", timeout=1)
    assert res["timed_out"] is True


def test_child_cannot_see_runner_secret(runner):
    # The runner's OWN env has FAKE_SECRET_TOKEN; the executed child must not.
    res = _call(runner, "python",
                "import os; print(os.environ.get('FAKE_SECRET_TOKEN', 'ABSENT'))")
    assert res["stdout"].strip() == "ABSENT"


def test_unknown_tool_rejected(runner):
    res = _call(runner, "perl", "print 1")
    assert res["error"] and "unknown tool" in res["error"]


def test_unavailable_socket_raises():
    with pytest.raises(SandboxUnavailable):
        asyncio.run(run_in_sandbox("bash", "echo hi", timeout=5,
                                   sock_path="/nonexistent/runner.sock"))
