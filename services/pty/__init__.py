"""Cross-platform pseudo-terminal sessions for Odysseus.

Provides a real interactive PTY on both Windows (via pywinpty / ConPTY) and
POSIX (via the stdlib pty module). Used by the /ws/pty WebSocket endpoint to
back an xterm.js terminal so progress bars (tqdm) and curses-style apps render
correctly — something a one-shot subprocess + SSE log tail cannot do.
"""

from .session import PtySession, PTY_BACKEND, pty_supported

__all__ = ["PtySession", "PTY_BACKEND", "pty_supported"]
