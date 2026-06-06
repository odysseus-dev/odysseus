"""Cross-platform OS compatibility helpers.

Odysseus began as a Linux/macOS/Docker-only app. This module centralizes the
small set of OS differences needed to run it *natively* on Windows so the rest
of the codebase can stay platform-agnostic. Import from here instead of
sprinkling ``os.name == "nt"`` checks (and POSIX-only calls) across modules.

Design rules:
  * Stdlib + ctypes only — no new third-party deps (no psutil/pywinpty).
  * POSIX behaviour is unchanged; Windows gets a faithful equivalent or a
    safe, documented no-op.
"""

from __future__ import annotations

import os
import ntpath
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

IS_WINDOWS = os.name == "nt"
IS_POSIX = not IS_WINDOWS


# ── File permissions ────────────────────────────────────────────────────────
def safe_chmod(path, mode: int) -> bool:
    """``os.chmod`` that is a harmless no-op on Windows.

    On POSIX we apply the mode — used to lock secret/key files down to 0o600.
    Windows has no POSIX permission bits; files under the user profile are
    already ACL-restricted to that user, so we skip rather than raise. Returns
    True when the mode was actually applied.
    """
    if IS_WINDOWS:
        return False
    try:
        os.chmod(path, mode)
        return True
    except OSError:
        return False


# ── Process detach / liveness / teardown ────────────────────────────────────
def detached_popen_kwargs() -> dict:
    """Keyword args for :class:`subprocess.Popen` that fully detach a child so
    it outlives the request/stream that launched it.

    POSIX: ``start_new_session=True`` (setsid) — new session + process group.
    Windows: ``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` — the child gets
    its own process group (so it isn't killed when the parent's console closes)
    and is detached from any console.
    """
    if IS_WINDOWS:
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
        return {"creationflags": flags}
    return {"start_new_session": True}


def pid_alive(pid: Optional[int]) -> bool:
    """True if a process with ``pid`` is currently running.

    POSIX uses the classic ``os.kill(pid, 0)`` probe. That is **unsafe on
    Windows**: CPython's ``os.kill`` calls ``TerminateProcess(handle, sig)`` for
    any signal other than CTRL_C/CTRL_BREAK, so ``os.kill(pid, 0)`` would *kill*
    the process it is checking. We instead open the process and read its exit
    code via the Win32 API.
    """
    if not pid:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def kill_process_tree(pid: Optional[int]) -> None:
    """Terminate ``pid`` and all of its descendants.

    POSIX: signal the whole process group (``killpg``), falling back to a plain
    ``kill`` if the pid isn't a group leader.
    Windows: ``taskkill /T /F`` walks and kills the child tree (there is no
    process-group signalling).
    """
    if not pid:
        return
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
        return
    import signal

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


# ── Shell / executable resolution ───────────────────────────────────────────
_BASH_CACHE: Optional[str] = None
_BASH_PROBED = False

# Common Git-for-Windows install locations to probe when bash isn't on PATH.
_WINDOWS_BASH_ROOT_ENV_VARS = (
    "ProgramFiles",
    "ProgramW6432",
    "ProgramFiles(x86)",
    "LocalAppData",
)
_WINDOWS_BASH_DEFAULT_ROOTS = (
    r"C:\Program Files\Git",
    r"C:\Program Files (x86)\Git",
)
_WINDOWS_BASH_RELATIVE_PATHS = (
    ("bin", "bash.exe"),
    ("usr", "bin", "bash.exe"),
)


def _windows_bash_fallbacks() -> List[str]:
    roots: List[str] = []
    for env_name in _WINDOWS_BASH_ROOT_ENV_VARS:
        base = os.environ.get(env_name)
        if base:
            roots.append(ntpath.join(base, "Git"))
    roots.extend(_WINDOWS_BASH_DEFAULT_ROOTS)

    paths: List[str] = []
    seen = set()
    for root in roots:
        for rel in _WINDOWS_BASH_RELATIVE_PATHS:
            path = ntpath.join(root, *rel)
            key = path.lower()
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return paths


def _is_windows_bash_stub(path: str) -> bool:
    lowered = path.lower()
    return (
        "system32\\bash.exe" in lowered
        or "sysnative\\bash.exe" in lowered
        or "windowsapps\\bash.exe" in lowered
    )


def git_bash_path(path: str | Path) -> str:
    """Convert a path to POSIX style suitable for Git Bash on Windows.

    Transforms drive letters (e.g., 'C:\\path') to POSIX '/c/path',
    and uses forward slashes.
    """
    p = Path(path)
    p_str = p.as_posix()
    if IS_WINDOWS and len(p_str) >= 2 and p_str[1] == ":":
        drive = p_str[0].lower()
        return f"/{drive}{p_str[2:]}"
    return p_str



def find_bash() -> Optional[str]:
    """Locate a real ``bash`` interpreter, or None.

    On Windows this is typically Git Bash / WSL. Many Odysseus features (the
    agent ``bash`` tool, background jobs, Cookbook scripts) emit bash syntax, so
    when a bash is present we use it and keep full parity with POSIX. Result is
    cached.
    """
    global _BASH_CACHE, _BASH_PROBED
    if _BASH_PROBED:
        return _BASH_CACHE
    _BASH_PROBED = True
    found = which_tool("bash")
    if found and IS_WINDOWS and _is_windows_bash_stub(found):
        found = None
    if not found and IS_WINDOWS:
        for cand in _windows_bash_fallbacks():
            if os.path.exists(cand):
                found = cand
                break
    _BASH_CACHE = found
    return found


def has_bash() -> bool:
    return find_bash() is not None


def which_tool(name: str) -> Optional[str]:
    """``shutil.which`` that also tries Windows executable suffixes.

    On Windows, Node/npm shims are ``npx.cmd``/``npm.cmd`` and binaries end in
    ``.exe``; a bare ``which("npx")`` can miss them depending on PATHEXT. We try
    the bare name first, then the common suffixes.
    """
    found = shutil.which(name)
    if found:
        return found
    if IS_WINDOWS:
        for ext in (".cmd", ".exe", ".bat"):
            found = shutil.which(name + ext)
            if found:
                return found
    return None


def run_script_argv(script_path) -> List[str]:
    """argv to execute a shell *script file*.

    Prefers bash (so existing ``.sh`` wrappers work verbatim, including on
    Windows via Git Bash). On Windows with no bash available, falls back to
    ``cmd.exe /c`` — simple commands still run, but bash-specific syntax won't.
    Callers that need guaranteed bash should check :func:`has_bash` first and
    surface a clear "install Git Bash" message.
    """
    bash = find_bash()
    if bash:
        return [bash, str(script_path)]
    if IS_WINDOWS:
        comspec = os.environ.get("ComSpec", "cmd.exe")
        return [comspec, "/c", str(script_path)]
    return ["sh", str(script_path)]
