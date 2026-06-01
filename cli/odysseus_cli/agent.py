"""Drive the Odysseus agent loop and translate its SSE stream to the terminal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from . import renderer as r
from .config import CliConfig, to_chat_completions_url

# Event types we forward to the renderer; everything else is ignored for the MVP.
_PASSTHROUGH = {
    "tool_start", "tool_progress", "tool_output", "agent_step",
    "metrics", "web_sources", "budget_exceeded",
}


def _parse_sse(chunk: str):
    """Yield parsed event dicts (or the string '[DONE]') from a raw SSE chunk."""
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            yield "[DONE]"
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


# Directories not worth showing the agent in a repo map.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "data", "logs",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".next", "target",
    ".idea", ".vscode",
}


def build_repo_map(root: Path, max_entries: int = 120) -> str:
    """A compact, depth-limited listing of the project tree for orientation."""
    root = Path(root)
    lines: list[str] = []
    try:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            parts = rel.parts
            if any(p in _SKIP_DIRS for p in parts):
                continue
            if len(parts) > 3:  # cap depth
                continue
            if path.name.startswith(".") and path.is_file():
                continue
            indent = "  " * (len(parts) - 1)
            suffix = "/" if path.is_dir() else ""
            lines.append(f"{indent}{path.name}{suffix}")
            if len(lines) >= max_entries:
                lines.append("… (truncated)")
                break
    except Exception:
        return ""
    return "\n".join(lines)


def build_project_context(root: Path) -> str:
    """A short system note giving the agent its bearings in the project."""
    lines = [
        "You are running as the Odysseus CLI: a local terminal coding agent.",
        f"The current project directory is: {root}",
        "Shell (bash) and file tools execute in this directory on the user's "
        "machine. Prefer relative paths. Make focused, minimal changes and "
        "explain what you did. When you edit files, show what changed.",
    ]
    for name in ("ODYSSEUS.md", "CLAUDE.md", "AGENTS.md"):
        f = root / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8")[:4000]
                lines.append(f"\nProject guide ({name}):\n{text}")
            except Exception:
                pass
    repo_map = build_repo_map(root)
    if repo_map:
        lines.append(f"\nProject files (depth-limited):\n{repo_map}")
    return "\n".join(lines)


def _headers(cfg: CliConfig) -> Optional[Dict[str, str]]:
    if cfg.api_key:
        return {"Authorization": f"Bearer {cfg.api_key}"}
    return None


async def run_turn(cfg: CliConfig, messages: List[Dict]) -> str:
    """Stream one agent turn. Renders output and returns the assistant text.

    `messages` already includes the latest user message. The returned assistant
    text should be appended to the history by the caller.
    """
    from src.agent_loop import stream_agent_loop

    r.assistant_prefix(cfg.model)
    assistant_text: List[str] = []
    pending_newline = False

    try:
        async for chunk in stream_agent_loop(
            to_chat_completions_url(cfg.endpoint),
            cfg.model,
            messages,
            headers=_headers(cfg),
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            max_rounds=cfg.max_rounds,
            context_length=cfg.context_length,
            owner=cfg.owner,
        ):
            for evt in _parse_sse(chunk):
                if evt == "[DONE]":
                    continue
                if "delta" in evt:
                    r.delta(evt["delta"])
                    assistant_text.append(evt["delta"])
                    pending_newline = True
                    continue
                etype = evt.get("type")
                if etype not in _PASSTHROUGH:
                    continue
                if pending_newline:
                    r.write()
                    pending_newline = False
                if etype == "tool_start":
                    r.tool_start(evt.get("tool", "?"), evt.get("command", ""),
                                 evt.get("round", 0))
                elif etype == "tool_progress":
                    r.tool_progress(evt.get("tool", "?"), evt.get("elapsed_s"),
                                    evt.get("tail", ""))
                elif etype == "tool_output":
                    r.tool_output(evt.get("tool", "?"), evt.get("output", ""),
                                  evt.get("exit_code"))
                elif etype == "agent_step":
                    r.agent_step(evt.get("round", 0))
                elif etype == "web_sources":
                    r.web_sources(evt.get("data", []))
                elif etype == "budget_exceeded":
                    r.info("tool budget exceeded — stopping.")
                elif etype == "metrics":
                    r.metrics(evt.get("data", {}))
    except KeyboardInterrupt:
        r.info("\n(interrupted)")
    finally:
        if pending_newline:
            r.write()

    return "".join(assistant_text)
