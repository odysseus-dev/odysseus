"""Terminal rendering for the Odysseus CLI.

Converts the agent loop's SSE events into colored terminal output. Uses plain
ANSI escapes (no extra dependencies) so it works in any terminal out of the box.
"""

from __future__ import annotations

import sys

# ── ANSI palette ──────────────────────────────────────────────────────────
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


_COLOR = _supports_color()


def c(text: str, color: str) -> str:
    """Colorize text if the terminal supports it."""
    if not _COLOR:
        return text
    return f"{color}{text}{RESET}"


# Optional output sink. When set (e.g. by the TUI), all rendered text is routed
# to this callable instead of stdout. Receives the already-ANSI-colored string.
_sink = None


def set_sink(fn) -> None:
    """Redirect rendered output to `fn(text)` (or back to stdout when None)."""
    global _sink, _COLOR
    _sink = fn
    if fn is not None:
        _COLOR = True  # force ANSI so the sink can colorize (e.g. Text.from_ansi)


def write(text: str = "", end: str = "\n") -> None:
    if _sink is not None:
        _sink(text)
        return
    sys.stdout.write(text + end)
    sys.stdout.flush()


def delta(text: str) -> None:
    """Stream a chunk of the assistant's reply inline (no newline)."""
    if _sink is not None:
        _sink(text)
        return
    sys.stdout.write(text)
    sys.stdout.flush()


# Odysseus brand color — a coral/salmon (256-color), distinct from Claude's orange.
SALMON = "\033[38;5;210m"


def _cell(text: str, width: int) -> str:
    """Left-justify to an exact visible width (text must be plain, no ANSI)."""
    return text[:width].ljust(width)


def welcome_box(version: str, model: str, root: str, approval: str,
                username: str = "") -> None:
    """A Claude-Code-style welcome panel, branded for Odysseus (salmon)."""
    inner = 70           # inner width between the side borders
    lw = 30              # left column width
    rw = inner - lw - 3  # right column width (3 = " │ ")

    def border(left_ch, fill, right_ch, title=""):
        if title:
            t = f" {title} "
            pad = inner - len(t) - 1
            line = (c(left_ch + "─", SALMON) + c(t, BOLD + SALMON)
                    + c(fill * pad + right_ch, SALMON))
            write(line)
        else:
            write(c(left_ch + fill * inner + right_ch, SALMON))

    def row(left: str, right: str, left_color: str = "", right_color: str = ""):
        lc = _cell(left, lw)
        rc = _cell(right, rw)
        if left_color:
            lc = c(lc, left_color)
        if right_color:
            rc = c(rc, right_color)
        write(c("│", SALMON) + " " + lc + c("│", SALMON) + " " + rc + " " + c("│", SALMON))

    hi = f"Welcome back, {username}!" if username else "Welcome aboard!"
    write()
    border("╭", "─", "╮", title=f"Odysseus CLI v{version}")
    row("", "")
    row(hi, "Getting started", left_color=BOLD, right_color=BOLD + SALMON)
    row("⊹ ૮( ˶ᵔ ᵕ ᵔ˶ )っ", "• ask me to read, edit, or fix code", left_color=SALMON, right_color=GREY)
    row("", "• /models   switch model", right_color=GREY)
    row(model, "• /new      start a fresh chat", left_color=CYAN, right_color=GREY)
    row(_short_path(root), "• /compact  free up context", left_color=GREY, right_color=GREY)
    row(f"approval: {approval}", "• /help     all commands · /exit", left_color=GREY, right_color=GREY)
    row("", "")
    border("╰", "─", "╯")
    write(c("  /help for commands · Ctrl-C to interrupt", GREY))
    write()


def _short_path(path: str) -> str:
    """Abbreviate a path with ~ for the home directory."""
    import os
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home):]
    return path


def banner(model: str, endpoint: str, root: str, approval: str) -> None:
    write()
    write(c("  ⊹ Odysseus CLI", BOLD + MAGENTA))
    write(c(f"    model     {model}", GREY))
    write(c(f"    endpoint  {endpoint}", GREY))
    write(c(f"    project   {root}", GREY))
    write(c(f"    approval  {approval}", GREY))
    write(c("    /help for commands · Ctrl-C to interrupt · /exit to quit", GREY))
    write()


def user_prompt() -> str:
    return c("\n❯ ", BOLD + CYAN)


def assistant_prefix(model: str) -> None:
    write(c(f"\n∞ {model}", BOLD + GREEN))


def tool_start(tool: str, command: str, round_num: int) -> None:
    head = c(f"  ⚙ {tool}", BOLD + YELLOW)
    body = command.strip()
    if len(body) > 400:
        body = body[:400] + " …"
    write(f"\n{head} {c('· round ' + str(round_num), GREY)}")
    for line in body.splitlines() or [body]:
        write(c("    " + line, DIM))


def tool_progress(tool: str, elapsed_s, tail: str) -> None:
    snippet = (tail or "").strip().splitlines()
    last = snippet[-1] if snippet else ""
    write(c(f"    … {tool} running {elapsed_s}s  {last[:80]}", GREY))


def tool_output(tool: str, output: str, exit_code) -> None:
    ok = exit_code in (0, None)
    mark = c("  ✓", GREEN) if ok else c("  ✗", RED)
    code = "" if exit_code is None else c(f" (exit {exit_code})", GREY)
    write(f"{mark} {c(tool, BOLD)}{code}")
    text = (output or "").rstrip()
    if not text:
        return
    lines = text.splitlines()
    shown = lines[:25]
    for line in shown:
        write(c("    " + line[:200], DIM))
    if len(lines) > len(shown):
        write(c(f"    … {len(lines) - len(shown)} more lines", GREY))


def agent_step(round_num: int) -> None:
    write(c(f"\n  ↻ thinking (round {round_num})…", GREY))


def metrics(data: dict) -> None:
    if not data:
        return
    parts = []
    for key in ("tokens", "total_tokens", "tool_calls", "rounds", "elapsed_s",
                "duration_s"):
        if key in data:
            parts.append(f"{key}={data[key]}")
    if parts:
        write(c("\n  " + "  ".join(parts), GREY))


def status_line(model: str, used_tokens: int, context_len: int) -> None:
    """Print a one-line footer: context usage on the left, model on the right."""
    import shutil
    width = shutil.get_terminal_size((80, 24)).columns
    pct = int(round(100 * used_tokens / context_len)) if context_len else 0
    pct_color = GREEN if pct < 60 else (YELLOW if pct < 85 else RED)
    left_plain = f"  {used_tokens:,}/{context_len:,} tok · {pct}% ctx"
    left = (f"  {c(f'{used_tokens:,}/{context_len:,} tok', GREY)} · "
            f"{c(f'{pct}% ctx', pct_color)}")
    right_plain = f"{model}  "
    right = c(f"{model}  ", DIM)
    pad = max(1, width - len(left_plain) - len(right_plain))
    write(left + " " * pad + right)


def error(msg: str) -> None:
    write(c(f"\n  ✗ {msg}", RED))


def info(msg: str) -> None:
    write(c(f"  {msg}", GREY))


def todos(items: list) -> None:
    """Render the agent's task checklist."""
    if not items:
        return
    write(c("\n  ☑ plan", BOLD + MAGENTA))
    for it in items:
        status = (it.get("status") or "pending").lower()
        content = it.get("content", "")
        if status == "completed":
            write(c(f"    [x] {content}", GREEN))
        elif status == "in_progress":
            write(c(f"    [~] {content}", YELLOW))
        else:
            write(c(f"    [ ] {content}", GREY))


def diff(lines: list) -> None:
    """Render a unified diff with colored +/- lines."""
    if not lines:
        write(c("    (no changes)", GREY))
        return
    shown = lines[:60]
    for line in shown:
        if line.startswith("+") and not line.startswith("+++"):
            write(c("    " + line[:200], GREEN))
        elif line.startswith("-") and not line.startswith("---"):
            write(c("    " + line[:200], RED))
        elif line.startswith("@@"):
            write(c("    " + line[:200], CYAN))
        else:
            write(c("    " + line[:200], GREY))
    if len(lines) > len(shown):
        write(c(f"    … {len(lines) - len(shown)} more diff lines", GREY))


def web_sources(sources: list) -> None:
    if not sources:
        return
    write(c("  🔎 sources:", GREY))
    for s in sources[:5]:
        title = s.get("title") or s.get("url") or ""
        write(c(f"    - {title[:90]}", GREY))
