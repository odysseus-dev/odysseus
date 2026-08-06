"""Regression guard for the Windows-local serve PID recording.

Git Bash's `$$` is the MSYS/Cygwin pid, NOT the Windows pid. When the local
serve runner recorded `$$`, the pid file held a value that Win32 tooling
(taskkill, Get-CimInstance ParentProcessId, Stop-Process) could not match, so
the frontend Stop-Tree walk found nothing and the llama-server child kept
running after Stop — pinning the GPU. The runner must record the shell's real
Windows pid via `/proc/$$/winpid` instead, falling back to the outer
`proc.pid` written from Python.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COOKBOOK_ROUTES = ROOT / "routes" / "cookbook_routes.py"


def _between(source, start, end):
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def test_local_windows_serve_records_windows_pid_not_msys_pid():
    source = COOKBOOK_ROUTES.read_text(encoding="utf-8")
    body = _between(
        source,
        "def _launch_local_detached(",
        '@router.post("/api/model/download")',
    )

    # The inner runner must resolve the true Win32 pid of the serving shell.
    assert "/proc/$$/winpid" in body, "runner must record the real Windows pid"

    # It must NOT record Git Bash's bare `$$` (the MSYS pid) as the pid — that
    # is the exact bug: the source used `printf '%s\\n' "$$" > <pidfile>`.
    assert '\\"$$\\"' not in body, "must not write bare MSYS $$ as the recorded pid"

    # The Python-side authoritative outer pid write remains as the fallback.
    assert "pid_path.write_text(str(proc.pid)" in body
