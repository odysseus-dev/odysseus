import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNING_JS = ROOT / "static" / "js" / "cookbookRunning.js"
COOKBOOK_JS = ROOT / "static" / "js" / "cookbook.js"


def _between(source, start, end):
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def test_windows_graceful_kill_uses_verified_native_process_tree_helper():
    source = RUNNING_JS.read_text(encoding="utf-8")
    wrapper = _between(source, "function _winPowerShellCmd(task, ps)", "function _winSessionStopTreePs(task)")
    helper = _between(source, "function _winSessionStopTreePs(task)", "function _tmuxGracefulKill(task)")
    graceful = _between(source, "function _tmuxGracefulKill(task)", "function _shQuote(value)")
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
    assert "function Test-Owned([int]$Id)" in helper
    assert ".CommandLine" in helper
    assert "refusing to force-kill" in helper
    assert helper.index("Test-Owned") < helper.index("taskkill.exe")
    assert "Get-NetTCPConnection -LocalPort $Prt -State Listen" in helper
    assert "Select-Object -ExpandProperty OwningProcess" in helper
    # Verification is scoped to an owned process still holding the port, not the
    # wrapper alone.
    assert "still held by a task process after kill" in helper
    # A genuinely dead task (no owned listener, no live PID) cleans up and exits
    # 0 so Remove can clear the row; a serve task with no resolvable port fails
    # closed rather than reporting an unverifiable stop as clean.
    assert "cannot verify shutdown" in helper
    assert helper.index("refusing to force-kill") < helper.index("Remove-Item")

    # Port resolution covers -p and Ollama forms (not just --port) and prefers a
    # backend-persisted authoritative port; identity keys off the model file/name.
    assert "task?.payload?.port ?? task?.port" in port_helper
    assert "--port" in port_helper
    assert "-p[=\\s]+" in port_helper
    assert "OLLAMA_HOST" in port_helper
    assert "11434" in port_helper
    assert ".gguf" in identity_helper
    assert "--model" in identity_helper

    # Routing unchanged.
    assert "${_shQuote(command)}" in wrapper
    assert "_winSessionStopTreePs(task)" in win_session
    assert "_winPowerShellCmd(task, ps)" in win_session
    assert "_winSessionStopTreePs(task)" in graceful
    assert "_winPowerShellCmd(task, ps)" in graceful
    assert "Stop-Process -Id $p -Force" not in graceful
    assert '-Filter "ParentProcessId = $Id"' not in helper
    assert 'powershell -Command \\\\"${ps}\\\\"' not in source


def _posix_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def test_remote_windows_stop_tree_payload_survives_shell_parsing():
    ps = (
        "$targets = [System.Collections.Generic.List[int]]::new(); "
        "function Add-Tree([int]$Id) { "
        "Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + $Id) "
        "-ErrorAction SilentlyContinue | ForEach-Object { Add-Tree ([int]$_.ProcessId) }; "
        "if (-not $targets.Contains($Id)) { $targets.Add($Id) } }; "
        "$port = 8000; foreach ($o in @(Get-NetTCPConnection -LocalPort $port -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess "
        "| Sort-Object -Unique)) { Add-Tree ([int]$o) }; "
        "$p = Get-Content '$env:TEMP\\odysseus-sessions\\serve_abc.pid' "
        "-ErrorAction SilentlyContinue; "
        "Add-Tree ([int]$p); "
        "foreach ($target in @($targets)) { & taskkill.exe /PID $target /T /F }"
    )
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
    assert "Get-NetTCPConnection -LocalPort $port -State Listen" in argv[-1]
    assert "Select-Object -ExpandProperty OwningProcess | Sort-Object -Unique" in argv[-1]


def test_persisted_local_task_platform_drives_windows_stop_routing():
    source = COOKBOOK_JS.read_text(encoding="utf-8")
    platform = _between(source, "export function _getPlatform(hostOrTask)", "export function _isWindows(hostOrTask)")

    assert "hostOrTask.platform || hostOrTask.payload?.platform" in platform
    assert "return taskPlatform || _envState.hostPlatform || ''" in platform


def test_stop_keeps_task_and_endpoint_until_process_exit_is_confirmed():
    source = RUNNING_JS.read_text(encoding="utf-8")
    helper = _between(source, "async function _stopTaskSession(task)", "// Force-kill escalation")
    stop_handler = _between(source, "// Wire stop", "// Wire kill")

    assert "commandOk = result?.exit_code === undefined || Number(result.exit_code) === 0" in helper
    assert "if (_isWindows(task) && !commandOk) return false" in helper
    assert "const stopped = await _stopTaskSession(task)" in stop_handler
    assert "if (!stopped)" in stop_handler
    assert "process exit was not confirmed" in stop_handler
    assert stop_handler.index("if (!stopped)") < stop_handler.index("_removeEndpointByUrl")
    assert stop_handler.index("if (!stopped)") < stop_handler.index("_animateOutThenRemove")
