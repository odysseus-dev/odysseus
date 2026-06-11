"""Cross-platform PTY session abstraction.

One class, two backends:
  - Windows: pywinpty (`from winpty import PtyProcess`) — wraps the native
    ConPTY pseudoconsole. Methods: spawn(cmd, dimensions=(rows, cols), cwd, env),
    read(size), write(data), setwinsize(rows, cols), isalive(), terminate().
  - POSIX: stdlib `pty.openpty()` + `os.fork`-free `subprocess` with the slave
    fd as stdio, plus `os.read`/`os.write` on the master fd.

The public API is intentionally tiny and identical on both platforms:
  session = PtySession(cwd=..., env=..., cols=80, rows=24)
  session.spawn()
  data = session.read()        # -> str (may be ""), non-blocking-ish
  session.write("ls\r\n")
  session.resize(cols, rows)
  session.alive                 # -> bool
  session.exit_code             # -> int | None
  session.kill()

Read is blocking on the underlying handle, so callers should run read() in a
thread/executor and not on the event loop. The WebSocket handler does exactly
that.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Optional, Mapping


IS_WINDOWS = os.name == "nt"


def _default_shell() -> str:
    """Pick a sensible interactive shell for the platform."""
    if IS_WINDOWS:
        # Prefer PowerShell 7 (pwsh), then Windows PowerShell, then cmd.
        for cand in ("pwsh.exe", "powershell.exe"):
            found = shutil.which(cand)
            if found:
                return found
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/bash")


def pty_supported() -> tuple[bool, Optional[str]]:
    """Return (supported, error_message). Imports the backend lazily."""
    if IS_WINDOWS:
        try:
            import winpty  # noqa: F401
            return True, None
        except Exception as exc:  # pragma: no cover - import guard
            return False, f"pywinpty unavailable: {exc}"
    # POSIX
    try:
        import pty  # noqa: F401
        return True, None
    except Exception as exc:  # pragma: no cover
        return False, f"pty module unavailable: {exc}"


_ok, _err = pty_supported()
PTY_BACKEND = ("winpty" if IS_WINDOWS else "pty") if _ok else None


class PtySession:
    """A single interactive pseudo-terminal process."""

    def __init__(
        self,
        cmd: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        cols: int = 80,
        rows: int = 24,
    ):
        self.cmd = cmd or _default_shell()
        self.cwd = cwd or os.path.expanduser("~")
        # Merge over the real environment so PATH etc. are present.
        merged = dict(os.environ)
        if env:
            merged.update(env)
        # Force a sane terminal type for POSIX curses apps.
        merged.setdefault("TERM", "xterm-256color")
        self.env = merged
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))

        self._winpty = None       # winpty.PtyProcess
        self._master_fd = None    # POSIX master fd
        self._proc = None         # POSIX subprocess.Popen
        self._exit_code: Optional[int] = None

    # ---- lifecycle -------------------------------------------------------

    def spawn(self) -> None:
        if IS_WINDOWS:
            self._spawn_windows()
        else:
            self._spawn_posix()

    def _spawn_windows(self) -> None:
        from winpty import PtyProcess
        # pywinpty's spawn() splits a string argv on spaces, which corrupts
        # shell paths like "C:\Program Files\PowerShell\7\pwsh.exe". Pass argv
        # as a LIST so the full path is preserved. dimensions is (rows, cols).
        argv = self.cmd if isinstance(self.cmd, list) else [self.cmd]
        self._winpty = PtyProcess.spawn(
            argv,
            dimensions=(self.rows, self.cols),
            cwd=self.cwd,
            env=self.env,
        )

    def _spawn_posix(self) -> None:
        import pty
        import subprocess

        master_fd, slave_fd = pty.openpty()
        try:
            import fcntl
            import termios
            import struct
            # Set initial window size on the pty.
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

        self._proc = subprocess.Popen(
            self.cmd,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.cwd,
            env=self.env,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            close_fds=True,
        )
        os.close(slave_fd)  # parent keeps only the master side
        # Make the master fd non-blocking so read() never hangs.
        import fcntl
        fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self._master_fd = master_fd

    # ---- io --------------------------------------------------------------

    def read(self, size: int = 4096) -> str:
        """Read available output. Returns "" when no data (or on benign EOF).

        Blocking on the underlying handle — call from a worker thread.
        """
        if IS_WINDOWS:
            if not self._winpty:
                return ""
            try:
                return self._winpty.read(size)
            except EOFError:
                self._capture_exit()
                return ""
            except Exception:
                self._capture_exit()
                return ""
        # POSIX
        if self._master_fd is None:
            return ""
        try:
            data = os.read(self._master_fd, size)
            if not data:
                self._capture_exit()
                return ""
            return data.decode("utf-8", errors="replace")
        except OSError:
            self._capture_exit()
            return ""

    def write(self, data: str) -> None:
        if IS_WINDOWS:
            if self._winpty and self._winpty.isalive():
                try:
                    self._winpty.write(data)
                except Exception:
                    pass
            return
        if self._master_fd is not None:
            try:
                os.write(self._master_fd, data.encode("utf-8", errors="replace"))
            except OSError:
                pass

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        self.cols, self.rows = cols, rows
        if IS_WINDOWS:
            if self._winpty:
                try:
                    self._winpty.setwinsize(rows, cols)  # (rows, cols)
                except Exception:
                    pass
            return
        if self._master_fd is not None:
            try:
                import fcntl
                import termios
                import struct
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    # ---- state -----------------------------------------------------------

    @property
    def alive(self) -> bool:
        if IS_WINDOWS:
            return bool(self._winpty and self._winpty.isalive())
        if self._proc is None:
            return False
        return self._proc.poll() is None

    @property
    def exit_code(self) -> Optional[int]:
        if self._exit_code is not None:
            return self._exit_code
        self._capture_exit()
        return self._exit_code

    def _capture_exit(self) -> None:
        if self._exit_code is not None:
            return
        if IS_WINDOWS:
            if self._winpty and not self._winpty.isalive():
                try:
                    self._exit_code = self._winpty.exitstatus
                except Exception:
                    self._exit_code = 0
                if self._exit_code is None:
                    self._exit_code = 0
        else:
            if self._proc is not None:
                rc = self._proc.poll()
                if rc is not None:
                    self._exit_code = rc

    def kill(self) -> None:
        if IS_WINDOWS:
            if self._winpty:
                try:
                    self._winpty.terminate(force=True)
                except Exception:
                    pass
        else:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except OSError:
                    pass
                self._master_fd = None
        self._capture_exit()
