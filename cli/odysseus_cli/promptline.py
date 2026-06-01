"""Rich input prompt via prompt_toolkit (with graceful fallback to input()).

Fixes the core problem with bare input(): pasting a multi-line block submits on
the first newline. prompt_toolkit uses bracketed paste, so a pasted block stays
in the buffer until you press Enter. It also gives:
  * a persistent bottom toolbar (model · tokens · context %)
  * command history (↑/↓)
  * Alt+Enter to insert a newline for manual multi-line composition

Unlike a full-screen TUI, prompt_toolkit only manages the prompt line — output
above scrolls normally and stays selectable/copyable in your terminal.
"""

from __future__ import annotations

from typing import Callable, Optional

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False


def available() -> bool:
    return _AVAILABLE


def make_session():
    """Create a PromptSession (or None if prompt_toolkit is unavailable)."""
    if not _AVAILABLE:
        return None
    kb = KeyBindings()

    @kb.add("escape", "enter")  # Alt/Option+Enter → newline (Enter still submits)
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return PromptSession(history=InMemoryHistory(), key_bindings=kb)


async def ask(session, prompt_ansi: str,
              toolbar_fn: Optional[Callable[[], str]] = None) -> str:
    """Prompt for input asynchronously. `toolbar_fn` returns the toolbar text."""
    def _toolbar():
        return ANSI(toolbar_fn()) if toolbar_fn else None

    return await session.prompt_async(
        ANSI(prompt_ansi),
        bottom_toolbar=_toolbar if toolbar_fn else None,
    )
