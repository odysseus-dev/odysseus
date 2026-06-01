"""Interactive arrow-key selector for the terminal (line mode).

Renders a list, lets the user move the highlight with ↑/↓, select with Enter,
and cancel with Esc/q. Falls back to a numbered prompt when stdin/stdout isn't
a TTY (tests, pipes), so automation still works.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from . import renderer as r


def _numbered_fallback(options: List[str], current: Optional[str],
                       prompt: str) -> Optional[str]:
    for i, opt in enumerate(options, 1):
        mark = r.c("  ← current", r.GREEN) if opt == current else ""
        r.info(f"  {i}. {opt}{mark}")
    try:
        sel = input(r.c(f"  {prompt} #  (Enter to cancel): ", r.CYAN)).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(options):
        return options[int(sel) - 1]
    return None


def select(options: List[str], current: Optional[str] = None,
           prompt: str = "Select") -> Optional[str]:
    """Interactively choose one option. Returns the choice, or None if cancelled."""
    if not options:
        return None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _numbered_fallback(options, current, prompt)

    import select as _sel
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stdout
    idx = options.index(current) if current in options else 0
    n = len(options)

    out.write(f"\r\n  {prompt}  ")
    out.write(r.c("(↑/↓ to move · Enter to select · Esc to cancel)\r\n", r.GREY))

    def draw():
        for i, opt in enumerate(options):
            tag = r.c("  ← current", r.GREY) if opt == current else ""
            if i == idx:
                out.write("\r" + r.c(f"  ❯ {opt}", r.BOLD + r.CYAN) + tag + "\033[K\r\n")
            else:
                out.write(f"\r    {opt}{tag}\033[K\r\n")
        out.flush()

    result: Optional[str] = None
    try:
        tty.setraw(fd)
        draw()
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":  # ESC — could be an arrow sequence or a bare Esc
                ready, _, _ = _sel.select([sys.stdin], [], [], 0.05)
                if not ready:
                    break  # bare Esc → cancel
                seq = sys.stdin.read(2)
                if seq == "[A":
                    idx = (idx - 1) % n
                elif seq == "[B":
                    idx = (idx + 1) % n
                else:
                    continue
            elif ch in ("\r", "\n"):
                result = options[idx]
                break
            elif ch in ("\x03", "q", "Q"):  # Ctrl-C or q → cancel
                break
            else:
                continue
            out.write(f"\033[{n}A")  # move cursor back to the top of the list
            draw()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\r\n")
        out.flush()
    return result
