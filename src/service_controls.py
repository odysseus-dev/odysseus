"""Local service controls: stop/restart the standalone Hermes WebUI and restart Odysseus itself.

Windows only. Every action is reached from a same-origin browser administrator (see application_modes).
Nothing here kills by name: the Hermes listener is found by its exact loopback port, must answer the Hermes
health identity, and must be a Python process; Odysseus is restarted by killing its own launcher tree from a
helper that is NOT part of that tree and then re-running the scheduled task that normally starts it. That
tree restart is refused when the Hermes listener runs INSIDE the launcher tree (the legacy launcher starts
the gateway on demand as a descendant): killing the tree would stop the gateway and any turn in progress.
scripts/restart-odysseus-app.ps1 is the reviewed path that protects the gateway branch in that layout.
"""
import functools
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
DEFAULT_TASK = 'Odysseus AutoStart'
DEFAULT_GATEWAY_PORT = 9130
_TASK_NAME = re.compile(r'^[A-Za-z0-9 _.\-]{1,100}$')
_LAUNCHER_MARK = 'launch-windows.ps1'
_PYTHON_IMAGES = ('python.exe', 'pythonw.exe')
RESTART_DELAY_SECONDS = 2


class Unavailable(RuntimeError):
    """The action cannot be done safely on this machine."""


class ActiveRuns(RuntimeError):
    def __init__(self, count, recent=False):
        if recent:        # the gateway only reports sessions touched in the last five minutes, not live runs
            super().__init__(f'{count} Hermes session(s) were active in the last five minutes; a run may still be in progress and stopping now would interrupt it.')
        else:
            super().__init__(f'{count} Hermes run(s) are still active; stopping now would interrupt them.')
        self.count = count


def supported():
    return os.name == 'nt'


def _run(command, timeout=20, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW, **kwargs)


def processes():
    """{pid: (ppid, image name, command line)} for every visible process."""
    script = ('Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine '
              '| ConvertTo-Json -Compress')
    result = _run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], timeout=30)
    if result.returncode or not result.stdout.strip():
        raise Unavailable('Windows would not list running processes.')
    rows = json.loads(result.stdout)
    rows = rows if isinstance(rows, list) else [rows]
    return {int(r['ProcessId']): (int(r['ParentProcessId'] or 0), str(r['Name'] or '').lower(), str(r['CommandLine'] or '')) for r in rows}


def listening_pids(port, netstat_output=None, *, fail_closed=False):
    """PIDs listening on this exact TCP port, and whether every listener is loopback-only.

    ``fail_closed`` is opted into only by the Odysseus restart guard: a FAILED live netstat
    probe then raises ``Unavailable`` (listener set UNKNOWN) instead of reading as "no
    listeners", so an unprobeable machine cannot let a tree restart proceed. Default callers
    keep the historical lenient semantics. Injected ``netstat_output`` is always trusted (no
    live probe runs, so there is no probe failure to detect).
    """
    if netstat_output is None:
        result = _run(['netstat.exe', '-ano', '-p', 'TCP'])
        if fail_closed and (result.returncode or not result.stdout.strip()):
            raise Unavailable('The netstat probe failed, so it is unknown whether the Hermes gateway runs inside the launcher tree; '
                              'refusing the tree restart rather than guess. Use scripts/restart-odysseus-app.ps1, which protects the gateway branch.')
        netstat_output = result.stdout
    pids, loopback_only = set(), True
    for line in netstat_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != 'TCP' or parts[3].upper() != 'LISTENING':
            continue
        host, _, listen_port = parts[1].rpartition(':')
        if listen_port != str(port):
            continue
        pids.add(int(parts[4]))
        loopback_only = loopback_only and host in ('127.0.0.1', '[::1]')
    return pids, loopback_only


def stop_hermes_listener(port, table=None, kill=None):
    """Force-stop the single loopback Python process that owns the Hermes port, with its children."""
    pids, loopback_only = listening_pids(port)
    if len(pids) != 1 or not loopback_only:
        raise Unavailable('The Hermes port is not owned by exactly one loopback-only process; nothing was stopped.')
    pid = next(iter(pids))
    table = table if table is not None else processes()
    image = table.get(pid, (0, '', ''))[1]
    if image not in _PYTHON_IMAGES:
        raise Unavailable(f'The Hermes port is owned by {image or "an unknown process"}, not Python; nothing was stopped.')
    (kill or kill_tree)(pid)
    return pid


def kill_tree(pid):
    from src.process_lifecycle import taskkill_tree
    taskkill_tree(int(pid))


def find_launcher(table, pid):
    """The PowerShell launcher (launch-windows.ps1) that is an ancestor of ``pid``, or None."""
    seen = set()
    while pid in table and pid not in seen:
        seen.add(pid)
        parent, name, command = table[pid]
        if name in ('powershell.exe', 'pwsh.exe') and _LAUNCHER_MARK in command:
            return pid
        pid = parent
    return None


def gateway_in_launcher_tree(launcher_pid, gateway_port, table=None, netstat_output=None):
    """True when a Hermes gateway listener on this port descends from the launcher.

    A ``taskkill /T`` of the launcher would then stop the gateway and any turn in
    progress. Every listener on the port is checked (a dual IPv4/IPv6 bind can list
    two PIDs); an absent gateway is not grounds to block because nothing would be
    killed. A FAILED live netstat probe is UNKNOWN, not "absent": the guard fails
    closed (``Unavailable``) rather than let a tree restart proceed unverified.
    Pure given injected ``table``/``netstat_output``.
    """
    pids, _ = listening_pids(gateway_port, netstat_output, fail_closed=True)
    if not pids:
        return False
    table = table if table is not None else processes()
    for gateway_pid in pids:
        seen = set()
        pid = gateway_pid
        while pid in table and pid not in seen:
            if pid == launcher_pid:
                return True
            seen.add(pid)
            pid = table[pid][0]
    return False


def _task_exists(task):
    return _run(['schtasks.exe', '/Query', '/TN', task]).returncode == 0


@functools.lru_cache(maxsize=1)
def odysseus_restart_plan():
    """(launcher pid, task name) when this Odysseus was started by its Windows launcher and task, else None."""
    if not supported():
        return None
    task = os.environ.get('ODYSSEUS_RESTART_TASK', DEFAULT_TASK)
    if not _TASK_NAME.match(task):
        return None
    try:
        launcher = find_launcher(processes(), os.getpid())
        return (launcher, task) if launcher and _task_exists(task) else None
    except (Unavailable, OSError, ValueError, subprocess.SubprocessError):
        return None


def availability(hermes_can_start):
    if not supported():
        return {'hermes_stop': False, 'hermes_restart': False, 'odysseus_restart': False}
    return {'hermes_stop': True, 'hermes_restart': bool(hermes_can_start), 'odysseus_restart': odysseus_restart_plan() is not None}


def restart_script(launcher_pid, task):
    if not isinstance(launcher_pid, int) or launcher_pid <= 4 or not _TASK_NAME.match(task):
        raise Unavailable('Refusing to build a restart helper from unvalidated values.')
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\r\n"
        f'Start-Sleep -Seconds {RESTART_DELAY_SECONDS}\r\n'
        f'& taskkill.exe /F /T /PID {launcher_pid}\r\n'
        'Start-Sleep -Seconds 3\r\n'
        f"Start-ScheduledTask -TaskName '{task}'\r\n"
        'Remove-Item -LiteralPath $PSCommandPath -Force\r\n'
    )


def schedule_odysseus_restart(plan=None, spawn=None, gateway_port=None):
    """Start the detached helper that restarts Odysseus; returns once the helper exists, before Odysseus dies.

    Refused when the Hermes listener on ``gateway_port`` runs inside the launcher tree: the helper's
    ``taskkill /T`` would stop it and any turn in progress. ``gateway_port`` defaults to the lab gateway
    port recorded in this app's environment, else the production default.
    """
    plan = plan if plan is not None else odysseus_restart_plan()
    if plan is None:
        raise Unavailable('Odysseus was not started by its Windows launcher and scheduled task, so it cannot restart itself.')
    launcher, task = plan
    if gateway_port is None:
        try:
            gateway_port = int(os.environ.get('ODYSSEUS_RESTART_LAB_GATEWAY_PORT') or DEFAULT_GATEWAY_PORT)
        except ValueError:
            raise Unavailable('The recorded lab gateway port is not a number; refusing to guess what a tree restart would stop.') from None
    if gateway_in_launcher_tree(launcher, gateway_port):
        raise Unavailable('The Hermes gateway is running inside the Odysseus launcher tree, so this restart would stop it and any turn in progress. '
                          'Use scripts/restart-odysseus-app.ps1, which protects the gateway branch.')
    descriptor, name = tempfile.mkstemp(prefix='odysseus-restart-', suffix='.ps1')
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as handle:
        handle.write(restart_script(launcher, task))
    command = f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{name}"'
    try:
        (spawn or spawn_outside_tree)(command)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return {'state': 'restarting', 'helper': name}


def spawn_outside_tree(command):
    """Create the process through WMI so its parent is not Odysseus: killing Odysseus's tree cannot reach it."""
    script = ('$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$env:ODYSSEUS_HELPER}; '
              'exit $r.ReturnValue')
    result = _run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], env={**os.environ, 'ODYSSEUS_HELPER': command})
    if result.returncode:
        raise Unavailable('Windows would not start the restart helper.')
