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
import sys
from pathlib import Path
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
  /model <name>    switch model (e.g. /model qwen2.5-coder:7b)
  /approval <m>    set approval policy: ask | auto | deny
  /clear           clear the conversation history
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


async def _drive(cfg, approval_state: ApprovalState, one_shot: str | None):
    from .agent import build_project_context, run_turn

    messages: List[Dict] = [
        {"role": "system", "content": build_project_context(cfg.project_root)},
    ]

    async def handle(user_text: str) -> None:
        messages.append({"role": "user", "content": user_text})
        reply = await run_turn(cfg, messages)
        messages.append({"role": "assistant", "content": reply})

    if one_shot is not None:
        await handle(one_shot)
        return

    r.banner(cfg.model, cfg.endpoint, str(cfg.project_root), approval_state.policy)
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
                r.info("history cleared.")
                continue
            if cmd == "model" and rest:
                cfg = cfg.with_overrides(model=rest)
                r.info(f"model → {rest}")
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

    approval_state = ApprovalState(cfg.approval)
    install_approval(approval_state)

    one_shot = " ".join(args.prompt).strip() if args.prompt else None
    try:
        asyncio.run(_drive(cfg, approval_state, one_shot))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
