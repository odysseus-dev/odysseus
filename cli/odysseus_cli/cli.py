"""Odysseus CLI entry point.

Usage:
  odysseus                      # interactive REPL in the current directory
  odysseus "fix the bug in x"   # one-shot: run a single task and exit
  odysseus --model llama3.2:3b --approval auto "..."

Run `odysseus --help` for all flags.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Importing readline transparently upgrades input() with arrow-key line editing,
# history (up/down), and emacs keybindings. No symbols are used directly.
try:
    import readline  # noqa: F401
except ImportError:  # pragma: no cover - readline absent on some platforms
    pass
from typing import Dict, List

from . import __version__
from . import bootstrap, renderer as r
from .approval import ApprovalState, install as install_approval
from .config import (
    APPROVAL_ASK, APPROVAL_AUTO, APPROVAL_DENY, load_config,
)

HELP = """\
commands:
  /help            show this help
  /models          list models installed at the endpoint (pick by number)
  /model <n|name>  switch model: /model 3  or  /model qwen2.5-coder:7b
  /approval <m>    set approval policy: ask | auto | deny
  /clear           clear the conversation history
  /compact         summarize history to free up context
  /save            save this conversation now
  /sessions        list recent saved sessions
  /exit, /quit     leave the CLI
"""


def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="odysseus", description="Local terminal coding agent (Odysseus).")
    p.add_argument("prompt", nargs="*", help="one-shot task; omit for REPL")
    p.add_argument("--model", help="model name served by the endpoint")
    p.add_argument("--endpoint", help="OpenAI-compatible base URL (…/v1)")
    p.add_argument("--project", help="project root for tools (default: cwd)")
    p.add_argument("--approval", choices=[APPROVAL_ASK, APPROVAL_AUTO, APPROVAL_DENY],
                   help="tool approval policy")
    p.add_argument("--yolo", action="store_true",
                   help="shortcut for --approval auto (no prompts)")
    p.add_argument("--read-only", action="store_true",
                   help="shortcut for --approval deny (no system mutations)")
    p.add_argument("--tui", action="store_true",
                   help="launch the full-screen TUI (scrollable transcript, "
                        "pinned status footer, input box)")
    p.add_argument("--legacy", action="store_true",
                   help="use the full Odysseus server agent loop (56 tools) "
                        "instead of the default lightweight native loop")
    p.add_argument("--resume", action="store_true",
                   help="resume the last conversation for this project")
    p.add_argument("--no-save", action="store_true",
                   help="do not persist this conversation to ~/.odysseus/sessions")
    p.add_argument("--version", action="version",
                   version=f"odysseus-cli {__version__}")
    return p.parse_args(argv)


def _resolve_config(args: argparse.Namespace):
    cfg = load_config()
    approval = args.approval
    if args.yolo:
        approval = APPROVAL_AUTO
    if args.read_only:
        approval = APPROVAL_DENY
    project = Path(args.project).resolve() if args.project else Path.cwd()
    return cfg.with_overrides(
        model=args.model,
        endpoint=args.endpoint,
        approval=approval,
        project_root=project,
    )


async def _drive(cfg, approval_state: ApprovalState, one_shot: str | None,
                 resume: bool = False, autosave: bool = True, legacy: bool = False):
    from . import models as models_mod
    from . import session
    from .agent import build_project_context

    project_context = build_project_context(cfg.project_root)
    if legacy:
        from .agent import run_turn as _run_turn
        async def run_turn(cfg, messages):
            return await _run_turn(cfg, messages)
        system_content = project_context
    else:
        from . import nativeagent
        async def run_turn(cfg, messages):
            return await nativeagent.run_turn(cfg, messages, approval_state)
        system_content = nativeagent.system_prompt(cfg.project_root, project_context)

    messages: List[Dict] = [
        {"role": "system", "content": system_content},
    ]
    if resume:
        prev = session.latest_for_root(cfg.project_root)
        if prev:
            data = session.load(prev)
            if data.get("messages"):
                messages = data["messages"]
                r.info(f"resumed {len(messages)} messages from {prev.name}")
        else:
            r.info("no previous session for this project — starting fresh.")

    def persist() -> None:
        if autosave:
            try:
                session.save(messages, cfg.model, cfg.project_root)
            except Exception as exc:
                r.info(f"(could not save session: {exc})")

    def show_status() -> None:
        u = approval_state.last_usage or {}
        used = u.get("total_tokens") or (
            u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
        if used:
            r.status_line(cfg.model, used, cfg.context_length)

    async def handle(user_text: str) -> None:
        approval_state.reset_calls()  # fresh duplicate-call tracker per turn
        messages.append({"role": "user", "content": user_text})
        reply = await run_turn(cfg, messages)
        messages.append({"role": "assistant", "content": reply})
        persist()
        show_status()

    if one_shot is not None:
        await handle(one_shot)
        return

    r.banner(cfg.model, cfg.endpoint, str(cfg.project_root), approval_state.policy)
    last_models: List[str] = []  # most recent /models listing, for numeric pick
    while True:
        try:
            line = input(r.user_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            r.write("\n" + r.c("  bye 👋", r.GREY))
            return
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line[1:].partition(" ")
            rest = rest.strip()
            if cmd in ("exit", "quit"):
                r.write(r.c("  bye 👋", r.GREY))
                return
            if cmd == "help":
                r.write(HELP)
                continue
            if cmd == "clear":
                del messages[1:]
                approval_state.last_usage = {}
                r.info("history cleared.")
                continue
            if cmd == "compact":
                if legacy:
                    r.info("/compact is available in the native loop only.")
                    continue
                from . import nativeagent
                r.info("compacting conversation…")
                messages[:] = await nativeagent.compact(cfg, messages)
                approval_state.last_usage = {}
                r.info(f"compacted → {len(messages)} message(s) kept.")
                continue
            if cmd == "save":
                try:
                    path = session.save(messages, cfg.model, cfg.project_root)
                    r.info(f"saved → {path}")
                except Exception as exc:
                    r.info(f"save failed: {exc}")
                continue
            if cmd == "sessions":
                rows = session.list_sessions()
                if not rows:
                    r.info("no saved sessions yet.")
                for s in rows:
                    r.info(f"{s['id']}  {s['messages']}msg  {s['model']}  {s['root']}")
                continue
            if cmd == "models":
                last_models = models_mod.list_models(cfg.endpoint, cfg.api_key)
                if not last_models:
                    r.info(f"no models found at {cfg.endpoint} "
                           "(is the server running?)")
                for i, name in enumerate(last_models, 1):
                    mark = "  (current)" if name == cfg.model else ""
                    r.info(f"  {i}. {name}{mark}")
                if last_models:
                    r.info("switch with /model <number> or /model <name>")
                continue
            if cmd == "model" and rest:
                target = rest
                # Numeric selection refers to the last `/models` listing.
                if rest.isdigit() and last_models:
                    idx = int(rest) - 1
                    if 0 <= idx < len(last_models):
                        target = last_models[idx]
                    else:
                        r.info(f"no model #{rest} (run /models to list)")
                        continue
                cfg = cfg.with_overrides(model=target)
                r.info(f"model → {target}")
                continue
            if cmd == "approval" and rest in (APPROVAL_ASK, APPROVAL_AUTO, APPROVAL_DENY):
                approval_state.policy = rest
                r.info(f"approval → {rest}")
                continue
            r.info(f"unknown command: /{cmd} (try /help)")
            continue
        await handle(line)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    cfg = _resolve_config(args)

    # Bring up the Odysseus runtime, unlock tools, and install the gate before
    # importing/running the agent loop.
    bootstrap.prepare()
    try:
        bootstrap.unlock_tools()
    except Exception as exc:  # pragma: no cover - import-time env issues
        r.error(f"failed to initialize Odysseus runtime: {exc}")
        return 2

    # Tools run relative to the project root — make it the working directory.
    try:
        os.chdir(cfg.project_root)
    except Exception as exc:
        r.error(f"cannot enter project root {cfg.project_root}: {exc}")
        return 2

    # Teach the parser the JSON tool-call format local coder models emit.
    try:
        from . import toolcompat
        toolcompat.install()
    except Exception as exc:  # pragma: no cover
        r.info(f"(tool-compat shim not installed: {exc})")

    # Full-screen TUI mode (native loop only). The TUI manages its own approval
    # state and rendering, so hand off here after the runtime is prepared.
    if args.tui:
        if args.legacy:
            r.error("--tui is not supported with --legacy (native loop only).")
            return 2
        try:
            from . import toolcompat
            toolcompat.install()
        except Exception:
            pass
        try:
            from . import tui
        except ImportError:
            r.error("the TUI needs Textual. Install it with: "
                    "pip install textual")
            return 2
        tui.run(cfg, resume=args.resume, autosave=not args.no_save)
        return 0

    approval_state = ApprovalState(cfg.approval, cfg.project_root)
    install_approval(approval_state)

    one_shot = " ".join(args.prompt).strip() if args.prompt else None
    try:
        asyncio.run(_drive(cfg, approval_state, one_shot,
                           resume=args.resume, autosave=not args.no_save,
                           legacy=args.legacy))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
