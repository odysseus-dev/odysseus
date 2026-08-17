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

    assert "function Add-Tree([int]$Id)" in helper
    assert "('ParentProcessId = ' + $Id)" in helper
    assert "Add-Tree ([int]$p)" in helper
    assert "taskkill.exe /PID $target /T /F" in helper
    assert "$alive.Count -gt 0" in helper
    assert "exit 1" in helper
    assert helper.index("$alive.Count -gt 0") < helper.index("Remove-Item")
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
