import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNING_JS = ROOT / "static" / "js" / "cookbookRunning.js"
COOKBOOK_JS = ROOT / "static" / "js" / "cookbook.js"
PORTS_JS = ROOT / "static" / "js" / "cookbookPorts.js"
_HAS_NODE = shutil.which("node") is not None
_POWERSHELL = shutil.which("powershell")


def _between(source, start, end):
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def _generated_stop_ps(task, options=None):
    source = RUNNING_JS.read_text(encoding="utf-8")
    identity = _between(
        source,
        "function _taskProcessIdentity(task)",
        "function _psLit(value)",
    )
    literal = _between(
        source,
        "function _psLit(value)",
        "function _winSessionStopTreePs(",
    )
    helper = _between(
        source,
        "function _winSessionStopTreePs(",
        "export function _tmuxGracefulKill(",
    )
    script = f"""
      import {{ taskServePort }} from {json.dumps(PORTS_JS.as_uri())};
      const _taskServePort = taskServePort;
      const _taskRemoteHost = task => task.remoteHost || task?.payload?.remote_host || '';
      {identity}
      {literal}
      {helper}
      console.log(JSON.stringify(_winSessionStopTreePs({json.dumps(task)}, {json.dumps(options or {})})));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _start_listener(identity):
    code = (
        "import socket,time; "
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('127.0.0.1',0)); s.listen(); "
        "print(s.getsockname()[1],flush=True); time.sleep(120)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, identity],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    port = int(proc.stdout.readline().strip())
    return proc, port


def _start_wrapper(session_dir, sid, identity=""):
    script = session_dir / f"{sid}.cmd"
    marker_arg = f" {identity}" if identity else ""
    script.write_text(
        f'@"{sys.executable}" -c "import time; time.sleep(120)"{marker_arg}\r\n',
        encoding="utf-8",
    )
    return subprocess.Popen(
        [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_powershell_wrapper(session_dir, sid, identity):
    script = session_dir / f"{sid}_run.ps1"
    script.write_text(
        f"& '{sys.executable}' -u -c 'import time; print(314159, flush=True); time.sleep(120)' '{identity}'\r\n",
        encoding="utf-8",
    )
    wrapper = subprocess.Popen(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    ready = wrapper.stdout.readline().strip()
    if ready != "314159":
        stderr = wrapper.stderr.read()
        _stop_process(wrapper)
        raise RuntimeError(f"PowerShell child did not start: {ready!r} {stderr!r}")
    return wrapper


def _start_powershell_listener_wrapper(session_dir, sid, identity):
    script = session_dir / f"{sid}_run.ps1"
    code = (
        "import socket,time; "
        "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); "
        "s.bind(('127.0.0.1',0)); s.listen(); "
        "print(s.getsockname()[1],flush=True); time.sleep(120)"
    )
    script.write_text(
        f'& \'{sys.executable}\' -c "{code}" \'{identity}\'\r\n',
        encoding="utf-8",
    )
    wrapper = subprocess.Popen(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    port = int(wrapper.stdout.readline().strip())
    return wrapper, port


def _stop_process(proc):
    if proc is None:
        return
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _remove_session_artifacts(session_dir, sid):
    for pattern in (f"{sid}.*", f"{sid}_*"):
        for artifact in session_dir.glob(pattern):
            artifact.unlink(missing_ok=True)


def test_windows_graceful_kill_uses_verified_native_process_tree_helper():
    source = RUNNING_JS.read_text(encoding="utf-8")
    wrapper = _between(source, "function _winPowerShellCmd(task, ps)", "function _winSessionStopTreePs(")
    helper = _between(source, "function _winSessionStopTreePs(", "function _tmuxGracefulKill(")
    graceful = _between(source, "function _tmuxGracefulKill(", "function _shQuote(value)")
    win_session = _between(source, "function _winSessionCmd(task, tmuxArgs)", "function _winPowerShellCmd(task, ps)")
    port_helper = _between(source, "function _taskServePort(task)", "function _taskProcessIdentity(task)")
    identity_helper = _between(source, "function _taskProcessIdentity(task)", "function _psLit(value)")

    # Native process-tree walk + force kill retained.
    assert "function Add-Tree([int]$Id)" in helper
    assert "('ParentProcessId = ' + $Id)" in helper
    assert "Add-Tree ([int]$p)" in helper
    assert "taskkill.exe /PID $target /T /F" in helper
    assert "$alive.Count -gt 0" in helper
    assert "exit 1" in helper
    # Final artifact cleanup happens only after the liveness verification.
    assert helper.index("$alive.Count -gt 0") < helper.rindex("Remove-Item")

    # Ownership binding: a port listener is force-killed only once its command
    # line is proven to belong to this task (identity match), and it is proven
    # before any kill runs. A bound port whose owner cannot be proven ours fails
    # closed and retains the task instead of killing an unrelated service.
    assert "function Test-Owned([int]$Id," in helper
    assert ".CommandLine" in helper
    assert "refusing to force-kill" in helper
    assert helper.index("Test-Owned") < helper.index("taskkill.exe")
    assert "Get-NetTCPConnection -LocalPort $Prt -State Listen" in helper
    assert "Select-Object -ExpandProperty OwningProcess" in helper
    assert "function Test-SessionRoot([int]$Id)" in helper
    assert "function Get-SessionRoot([int]$Id)" in helper
    assert "(Test-Owned ([int]$o)) -and $root -gt 0" in helper
    assert "if (Test-SessionRoot ([int]$p))" in helper
    assert "(Test-SessionRoot ([int]$p)) -or (Test-Owned" not in helper
    assert "$unsafePid" in helper
    assert "netstat.exe -ano -p TCP" in helper
    assert "$remainingListeners.Count -gt 0" in helper
    assert helper.index("if ($unverified)") < helper.index("taskkill.exe")
    # A genuinely dead task (no owned listener, no live PID) cleans up and exits
    # 0 so Remove can clear the row; a serve task with no resolvable port fails
    # closed rather than reporting an unverifiable stop as clean.
    assert "cannot verify shutdown" in helper
    assert "$allowDeadServeCleanup" in helper
    assert "Matching model process still exists after kill" in helper
    assert helper.index("refusing to force-kill") < helper.index("Remove-Item")

    # Port resolution covers -p and Ollama forms (not just --port) and prefers a
    # backend-persisted authoritative port; identity keys off the model file/name.
    assert "return taskServePort(task)" in port_helper
    assert ".gguf" in identity_helper
    assert "--model" in identity_helper

    # Routing unchanged.
    assert "${_shQuote(command)}" in wrapper
    assert "_winSessionStopTreePs(task)" in win_session
    assert "_winPowerShellCmd(task, ps)" in win_session
    assert "_winSessionStopTreePs(task, options)" in graceful
    assert "_winPowerShellCmd(task, ps)" in graceful
    assert "Stop-Process -Id $p -Force" not in graceful
    assert '-Filter "ParentProcessId = $Id"' not in helper
    assert 'powershell -Command \\\\"${ps}\\\\"' not in source


def _posix_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def test_remote_windows_stop_tree_payload_survives_shell_parsing():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")
    ps = _generated_stop_ps({
        "sessionId": "serve_abc",
        "type": "serve",
        "remoteHost": "winbox",
        "payload": {"_cmd": "llama-server --model model.gguf --port 8000"},
    })
    remote_command = f'powershell -Command "{ps}"'
    shell_command = f"ssh -p 2222 winbox {_posix_quote(remote_command)}"

    argv = shlex.split(shell_command)

    assert argv == ["ssh", "-p", "2222", "winbox", remote_command]
    assert "$Id" in argv[-1]
    assert "$_.ProcessId" in argv[-1]
    assert "$env:TEMP" in argv[-1]
    assert "$p" in argv[-1]
    assert "taskkill.exe /PID $target /T /F" in argv[-1]
    # The pipe-heavy port-owner lookup must survive SSH single-quoting + shlex.
    assert "Get-NetTCPConnection -LocalPort $Prt -State Listen" in argv[-1]
    assert "Select-Object -ExpandProperty OwningProcess" in argv[-1]


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_refuses_unverified_listener_with_live_wrapper():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    listener, port = _start_listener("unrelated-listener")
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper = _start_wrapper(session_dir, sid)
    pid_path = session_dir / f"{sid}.pid"
    log_path = session_dir / f"{sid}.log"
    pid_path.write_text(str(wrapper.pid), encoding="utf-8")
    log_path.write_text("validation", encoding="utf-8")
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": f"llama-server --model expected-{sid}.gguf --port {port}"},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "unverified process" in (result.stderr + result.stdout)
        assert listener.poll() is None
        assert wrapper.poll() is None
        assert pid_path.exists()
        assert log_path.exists()
    finally:
        _stop_process(listener)
        _stop_process(wrapper)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_kills_verified_listener_and_wrapper():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    identity = f"owned-{sid}.gguf"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper, port = _start_powershell_listener_wrapper(session_dir, sid, identity)
    pid_path = session_dir / f"{sid}.pid"
    log_path = session_dir / f"{sid}.log"
    pid_path.write_text(str(wrapper.pid), encoding="utf-8")
    log_path.write_text("validation", encoding="utf-8")
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": f"llama-server --model {identity} --port {port}"},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        wrapper.wait(timeout=10)
        assert not pid_path.exists()
        assert not log_path.exists()
        assert not (session_dir / f"{sid}_run.ps1").exists()
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        _stop_process(wrapper)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_ignores_same_model_in_another_session():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    other_sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    identity = f"shared-{uuid.uuid4().hex[:8]}.gguf"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper, port = _start_powershell_listener_wrapper(session_dir, sid, identity)
    other_wrapper, other_port = _start_powershell_listener_wrapper(
        session_dir, other_sid, identity
    )
    (session_dir / f"{sid}.pid").write_text(str(wrapper.pid), encoding="utf-8")
    (session_dir / f"{other_sid}.pid").write_text(
        str(other_wrapper.pid), encoding="utf-8"
    )
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": f"llama-server --model {identity} --port {port}"},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        wrapper.wait(timeout=10)
        assert other_wrapper.poll() is None
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", other_port)) == 0
    finally:
        _stop_process(wrapper)
        _stop_process(other_wrapper)
        _remove_session_artifacts(session_dir, sid)
        _remove_session_artifacts(session_dir, other_sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_owns_dynamic_gguf_by_llama_engine_and_session():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper, port = _start_powershell_listener_wrapper(
        session_dir, sid, "llama-server"
    )
    pid_path = session_dir / f"{sid}.pid"
    pid_path.write_text(str(wrapper.pid), encoding="utf-8")
    snapshots = '"$HOME/.cache/huggingface/hub/models--org--model/snapshots"'
    dynamic_cmd = (
        f"MODEL_FILE=$({{ find {snapshots} -name '*-00001-of-*.gguf' 2>/dev/null | sort; "
        f"find {snapshots} -name '*.gguf' 2>/dev/null | sort; }} | head -1) && "
        f'llama-server --model "$MODEL_FILE" --host 0.0.0.0 --port {port}'
    )
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": dynamic_cmd},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        wrapper.wait(timeout=10)
        assert not pid_path.exists()
    finally:
        _stop_process(wrapper)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.parametrize("reuse_listener_as_recorded_pid", [False, True])
@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_refuses_matching_replacement_without_session_root(
    reuse_listener_as_recorded_pid,
):
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    identity = f"owned-{sid}.gguf"
    listener, port = _start_listener(identity)
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    pid_path = session_dir / f"{sid}.pid"
    log_path = session_dir / f"{sid}.log"
    if reuse_listener_as_recorded_pid:
        pid_path.write_text(str(listener.pid), encoding="utf-8")
    log_path.write_text("validation", encoding="utf-8")
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": f"llama-server --model {identity} --port {port}"},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "refusing" in (result.stderr + result.stdout)
        assert listener.poll() is None
        assert log_path.exists()
    finally:
        _stop_process(listener)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_only_cleans_dead_serve_when_remove_allows_it():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    log_path = session_dir / f"{sid}.log"
    log_path.write_text("terminal", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    task = {
        "sessionId": sid,
        "type": "serve",
        "status": "error",
        "payload": {"_cmd": f"llama-server --model dead-{sid}.gguf --port {port}"},
    }
    try:
        refused = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _generated_stop_ps(task)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert refused.returncode != 0
        assert log_path.exists()

        removed = subprocess.run(
            [
                _POWERSHELL,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _generated_stop_ps(task, {"allowDeadServeCleanup": True}),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert removed.returncode == 0, removed.stderr + removed.stdout
        assert not log_path.exists()
    finally:
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_finds_matching_prebind_process_in_sibling_runner_tree():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    identity = f"prebind-{sid}.gguf"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    recorded_wrapper = _start_wrapper(session_dir, sid)
    sibling_wrapper = None
    try:
        sibling_wrapper = _start_powershell_wrapper(session_dir, sid, identity)
        pid_path = session_dir / f"{sid}.pid"
        pid_path.write_text(str(recorded_wrapper.pid), encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            unused_port = probe.getsockname()[1]
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {"_cmd": f"llama-server --model {identity} --port {unused_port}"},
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        recorded_wrapper.wait(timeout=10)
        sibling_wrapper.wait(timeout=10)
        assert not pid_path.exists()
        assert not (session_dir / f"{sid}_run.ps1").exists()
    finally:
        _stop_process(recorded_wrapper)
        _stop_process(sibling_wrapper)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_keeps_shared_ollama_listener_and_kills_only_wrapper():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    listener, port = _start_listener("shared-ollama-daemon")
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper = _start_wrapper(session_dir, sid)
    pid_path = session_dir / f"{sid}.pid"
    pid_path.write_text(str(wrapper.pid), encoding="utf-8")
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {
                "_cmd": "docker exec ollama-rocm ollama show llama3",
                "runtime_port": str(port),
            },
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        wrapper.wait(timeout=10)
        assert listener.poll() is None
        assert not pid_path.exists()
    finally:
        _stop_process(listener)
        _stop_process(wrapper)
        _remove_session_artifacts(session_dir, sid)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_refuses_serve_with_unknown_port():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    ps = _generated_stop_ps({
        "sessionId": sid,
        "type": "serve",
        "payload": {"_cmd": "python custom_server.py", "repo_id": "org/model"},
    })
    result = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "no resolvable port" in (result.stderr + result.stdout)


@pytest.mark.skipif(
    os.name != "nt" or not _HAS_NODE or not _POWERSHELL,
    reason="native Windows, node, and Windows PowerShell are required",
)
def test_generated_windows_stop_clears_pip_maintenance_without_a_port():
    sid = f"serve-test-{uuid.uuid4().hex[:8]}"
    session_dir = Path(os.environ["TEMP"]) / "odysseus-tmux"
    session_dir.mkdir(parents=True, exist_ok=True)
    wrapper = _start_wrapper(session_dir, sid)
    pid_path = session_dir / f"{sid}.pid"
    log_path = session_dir / f"{sid}.log"
    pid_path.write_text(str(wrapper.pid), encoding="utf-8")
    log_path.write_text("validation", encoding="utf-8")
    try:
        ps = _generated_stop_ps({
            "sessionId": sid,
            "type": "serve",
            "payload": {
                "repo_id": "pip-update",
                "_cmd": "python -m pip install -U vllm transformers",
            },
        })
        result = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        wrapper.wait(timeout=10)
        assert not pid_path.exists()
        assert not log_path.exists()
    finally:
        _stop_process(wrapper)
        _remove_session_artifacts(session_dir, sid)


def test_persisted_local_task_platform_drives_windows_stop_routing():
    source = COOKBOOK_JS.read_text(encoding="utf-8")
    platform = _between(source, "export function _getPlatform(hostOrTask)", "export function _isWindows(hostOrTask)")

    assert "hostOrTask.platform || hostOrTask.payload?.platform" in platform
    assert "return taskPlatform || _envState.hostPlatform || ''" in platform


def test_stop_keeps_task_and_endpoint_until_process_exit_is_confirmed():
    source = RUNNING_JS.read_text(encoding="utf-8")
    helper = _between(source, "async function _stopTaskSession(", "// Force-kill escalation")
    stop_helper = _between(source, "async function _stopTaskFromElement", "// Force-kill escalation")
    stop_handler = _between(source, "// Wire stop", "// Wire kill")

    assert "commandOk = result?.exit_code === undefined || Number(result.exit_code) === 0" in helper
    assert "if (_isWindows(task) && !commandOk) return false" in helper
    assert "const stopped = await _stopTaskSession(task," in stop_helper
    assert "if (!stopped)" in stop_helper
    assert "process exit was not confirmed" in stop_helper
    assert "await _taskSessionOwnershipConfirmed(task)" in stop_helper
    assert stop_helper.index("if (!stopped)") < stop_helper.index("_removeEndpointByUrl")
    assert stop_helper.index("if (!stopped)") < stop_helper.index("_animateOutThenRemove")
    assert "await _stopTaskFromElement(el, task)" in stop_handler

    kill_handler = _between(source, "// Wire kill", "// Wire retry")
    assert "await _stopTaskFromElement(el, task)" in kill_handler
    assert "_ollamaUnloadCommand" not in kill_handler
    assert "/api/model-endpoints" not in kill_handler
    assert "eps.find" not in kill_handler


def test_stop_all_awaits_each_verified_result_before_reporting_success():
    source = RUNNING_JS.read_text(encoding="utf-8")
    stop_all = _between(source, '// Wire "Stop all" buttons', "// Section collapse/expand")

    assert "await _stopTaskFromElement" in stop_all
    assert "_isStoppableTask(t)" in stop_all
    assert "failedCount" in stop_all
    assert "could not be confirmed" in stop_all
    assert ".click()" not in stop_all

    live_predicate = _between(source, "function _isStoppableTask", "function _hasLiveTasks")
    assert "task.status === 'ready'" in live_predicate


def test_hardware_fit_collision_waits_for_verified_stop_result():
    source = (ROOT / "static" / "js" / "cookbook-hwfit.js").read_text(encoding="utf-8")
    quick_run = _between(source, "// ─── Pre-launch: stop colliding serves", "// -- Launch")

    assert "await _stopTaskFromElement" in quick_run
    assert "could not be confirmed stopped" in quick_run
    assert "_stopBtn.click()" not in quick_run
    assert "_tmuxGracefulKill" not in quick_run
    assert "setTimeout(r, 2500)" not in quick_run


def test_remediation_paths_route_through_verified_stop():
    # Restart, serve auto-fix/retry, download auto-retry, and same-port replace
    # must gate the relaunch on the verified stop result and retain the task when
    # termination is not confirmed — never kill-and-relaunch into a bound port.
    source = RUNNING_JS.read_text(encoding="utf-8")
    retry = _between(source, "async function _retryTask(el, task)", "async function _retryDownload(")
    autofix = _between(source, "export async function _serveAutoFix(", "async function _openServeEditForTask(")
    launch = _between(source, "if (_replaceTaskId) {", "// Capture the env + GPU pin")

    # Restart/retry verifies the stop and bails on failure, no direct kill.
    assert "await _stopTaskSession(task)" in retry
    assert "Restart aborted" in retry
    assert "_tmuxGracefulKill(task)" not in retry

    # Serve auto-fix gates the relaunch and re-enables the panel on failure.
    assert "await _stopTaskSession(task)" in autofix
    assert "_unguardServeRetry(panel, taskEl)" in autofix
    assert "_tmuxCmd(task, `kill-session" not in autofix

    # Launch-time replace of the old / same-port server verifies before relaunch.
    assert "await _stopTaskSession(_old)" in launch
    assert "await _stopTaskSession(_t)" in launch
    assert "_tmuxGracefulKill(_old)" not in launch
    assert "_tmuxGracefulKill(_t)" not in launch
