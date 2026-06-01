"""Full-screen TUI for the Odysseus CLI (Textual).

A Claude-Code-style terminal UI: a scrollable transcript pane, a pinned status
footer (model · tokens · context %), and a persistent input box. The agent runs
as a Textual worker; rendered output is routed into the transcript via the
renderer's sink, and tool approvals use a modal instead of an input() prompt.

The line-based CLI (cli.py) remains the default; this is opt-in via `--tui`.
"""

from __future__ import annotations

from typing import Dict, List

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from . import nativeagent, renderer as r, session
from .agent import build_project_context
from .approval import ApprovalState
from . import approval as _approval
from . import models as models_mod
from .config import APPROVAL_AUTO, APPROVAL_DENY


class ApprovalModal(ModalScreen):
    """Asks the user to approve a tool call. Dismisses with yes/no/always."""

    BINDINGS = [
        Binding("y", "yes", "Yes"),
        Binding("n", "no", "No"),
        Binding("a", "always", "Always"),
        Binding("escape", "no", "No"),
    ]

    def __init__(self, tool: str, preview: str, diff_lines=None):
        super().__init__()
        self._tool = tool
        self._preview = preview
        self._diff = diff_lines

    def compose(self) -> ComposeResult:
        body = Text()
        body.append(f"Allow {self._tool}?\n\n", style="bold yellow")
        if self._diff:
            for line in self._diff[:40]:
                if line.startswith("+") and not line.startswith("+++"):
                    body.append(line + "\n", style="green")
                elif line.startswith("-") and not line.startswith("---"):
                    body.append(line + "\n", style="red")
                elif line.startswith("@@"):
                    body.append(line + "\n", style="cyan")
                else:
                    body.append(line + "\n", style="dim")
        else:
            for line in (self._preview or "").splitlines()[:12]:
                body.append(line + "\n", style="dim")
        body.append("\n[y]es   [n]o   [a]lways", style="bold")
        yield Container(Static(body, id="approval-body"), id="approval-box")

    def action_yes(self) -> None:
        self.dismiss("yes")

    def action_no(self) -> None:
        self.dismiss("no")

    def action_always(self) -> None:
        self.dismiss("always")


class OdysseusTUI(App):
    """The Odysseus CLI as a full-screen Textual app."""

    CSS = """
    Screen { layout: vertical; }
    #transcript { height: 1fr; border: round $primary; padding: 0 1; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #prompt { height: 3; border: round $accent; }
    #approval-box {
        align: center middle; width: 80%; height: auto; max-height: 80%;
        border: round $warning; background: $surface; padding: 1 2;
    }
    """

    BINDINGS = [Binding("ctrl+c", "quit", "Quit")]

    def __init__(self, cfg, resume: bool = False, autosave: bool = True):
        super().__init__()
        self.cfg = cfg
        self.autosave = autosave
        self.state = ApprovalState(cfg.approval, cfg.project_root)
        self.messages: List[Dict] = []
        self._resume = resume
        self._busy = False
        self._last_models: List[str] = []

    # ── layout ─────────────────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        yield Static("", id="status")
        yield Input(placeholder="Message Odysseus…  (/help, /exit)", id="prompt")

    def on_mount(self) -> None:
        self.log_widget = self.query_one("#transcript", RichLog)
        self.status_widget = self.query_one("#status", Static)
        # Route renderer output into the transcript, and approvals into a modal.
        r.set_sink(self._sink)
        _approval.set_prompt(self._approve_modal)

        project_context = build_project_context(self.cfg.project_root)
        self.messages = [{"role": "system",
                          "content": nativeagent.system_prompt(self.cfg.project_root,
                                                               project_context)}]
        if self._resume:
            prev = session.latest_for_root(self.cfg.project_root)
            if prev:
                data = session.load(prev)
                if data.get("messages"):
                    self.messages = data["messages"]
                    r.info(f"resumed {len(self.messages)} messages")
        self._banner()
        self._update_status()
        self.query_one("#prompt", Input).focus()

    # ── output plumbing ─────────────────────────────────────────────────────
    def _sink(self, text: str) -> None:
        self.log_widget.write(Text.from_ansi(text))

    def _banner(self) -> None:
        r.write(r.c("  ⊹ Odysseus CLI — TUI", r.BOLD + r.MAGENTA))
        r.info(f"model {self.cfg.model} · {self.cfg.project_root}")
        r.info("/help for commands · Ctrl-C to quit")

    def _used_tokens(self) -> int:
        u = self.state.last_usage or {}
        return u.get("total_tokens") or (
            u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))

    def _update_status(self) -> None:
        used = self._used_tokens()
        ctx = self.cfg.context_length or 1
        pct = int(round(100 * used / ctx))
        color = "green" if pct < 60 else ("yellow" if pct < 85 else "red")
        line = Text()
        line.append(f"{used:,}/{ctx:,} tok ", style="dim")
        line.append(f"· {pct}% ctx", style=color)
        line.append(f"   {self.cfg.approval}", style="dim")
        line.append(f"   {self.cfg.model}", style="dim")
        self.status_widget.update(line)

    # ── approval modal as the prompt implementation ─────────────────────────
    async def _approve_modal(self, tool: str, preview: str, diff_lines=None) -> str:
        if self.cfg.approval == APPROVAL_AUTO:
            return "yes"
        if self.cfg.approval == APPROVAL_DENY:
            return "no"
        return await self.push_screen_wait(ApprovalModal(tool, preview, diff_lines))

    # ── input handling ──────────────────────────────────────────────────────
    @on(Input.Submitted, "#prompt")
    def _on_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        if not text or self._busy:
            return
        if text.startswith("/"):
            self._slash(text)
            return
        r.write(r.c(f"\n❯ {text}", r.BOLD + r.CYAN))
        self._run_turn(text)

    def _slash(self, line: str) -> None:
        cmd, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        if cmd in ("exit", "quit"):
            self.exit()
        elif cmd == "help":
            r.write("commands: /models /model <n|name> /approval <ask|auto|deny> "
                    "/compact /clear /save /sessions /exit")
        elif cmd == "clear":
            del self.messages[1:]
            self.state.last_usage = {}
            r.info("history cleared.")
            self._update_status()
        elif cmd == "models":
            self._last_models = models_mod.list_models(self.cfg.endpoint, self.cfg.api_key)
            for i, name in enumerate(self._last_models, 1):
                mark = "  (current)" if name == self.cfg.model else ""
                r.info(f"  {i}. {name}{mark}")
        elif cmd == "model" and rest:
            target = rest
            if rest.isdigit() and self._last_models:
                idx = int(rest) - 1
                if 0 <= idx < len(self._last_models):
                    target = self._last_models[idx]
            self.cfg = self.cfg.with_overrides(model=target)
            r.info(f"model → {target}")
            self._update_status()
        elif cmd == "approval" and rest in ("ask", "auto", "deny"):
            self.cfg = self.cfg.with_overrides(approval=rest)
            self.state.policy = rest
            r.info(f"approval → {rest}")
            self._update_status()
        elif cmd == "compact":
            self._compact()
        elif cmd == "save":
            session.save(self.messages, self.cfg.model, self.cfg.project_root)
            r.info("saved.")
        elif cmd == "sessions":
            for s in session.list_sessions():
                r.info(f"{s['id']}  {s['messages']}msg  {s['model']}")
        else:
            r.info(f"unknown command: /{cmd}")

    @work(exclusive=True)
    async def _run_turn(self, text: str) -> None:
        self._busy = True
        try:
            self.state.reset_calls()
            self.messages.append({"role": "user", "content": text})
            reply = await nativeagent.run_turn(self.cfg, self.messages, self.state)
            self.messages.append({"role": "assistant", "content": reply})
            if self.autosave:
                try:
                    session.save(self.messages, self.cfg.model, self.cfg.project_root)
                except Exception:
                    pass
        finally:
            self._busy = False
            self._update_status()

    @work(exclusive=True)
    async def _compact(self) -> None:
        r.info("compacting…")
        self.messages[:] = await nativeagent.compact(self.cfg, self.messages)
        self.state.last_usage = {}
        r.info(f"compacted → {len(self.messages)} message(s).")
        self._update_status()


def run(cfg, resume: bool = False, autosave: bool = True) -> None:
    """Launch the TUI."""
    OdysseusTUI(cfg, resume=resume, autosave=autosave).run()
