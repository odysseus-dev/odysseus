"""A dedicated, minimal coding agent loop — the "Claude Code flow".

Why this exists: routing a local model through Odysseus's full server harness
(56 tools, hybrid native+fenced format, MCP email/calendar/cookbook) overwhelms
small models — they pick the wrong tool and loop. Robust coding agents do the
opposite: a *tight* toolset, *native* structured tool-calls, and *properly
threaded* tool turns (assistant.tool_calls -> tool message with tool_call_id).

This module talks directly to the OpenAI-compatible endpoint (Ollama) with a
fixed set of six coding tools and clean turn threading. It reuses the CLI's
sandbox (path containment + diffs) and approval gate.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List

import httpx

from . import renderer as r
from . import sandbox
from . import approval as _approval
from .approval import ApprovalState
from .config import (
    APPROVAL_AUTO, APPROVAL_DENY, CliConfig, to_chat_completions_url,
)

MAX_TOOL_OUTPUT = 8000
MAX_READ_CHARS = 20000

# ── The fixed coding toolset (native OpenAI tool schemas) ──────────────────
TOOLS: List[Dict] = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file's contents. Path is relative to the project root.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "File path relative to the project root."}
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files and folders in a directory (relative to the project root).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory path. Default '.'"}
        }}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "Search files for a regular-expression pattern. Returns matching lines with file:line.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "File or directory to search. Default '.'"}
        }, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content. Shows a diff and asks the user to approve.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact substring in a file with new text. Use for surgical edits. Asks for approval.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "find": {"type": "string", "description": "Exact text to find (must be unique in the file)."},
            "replace": {"type": "string", "description": "Replacement text."}
        }, "required": ["path", "find", "replace"]}}},
    {"type": "function", "function": {
        "name": "bash",
        "description": "Run a shell command in the project root. Asks for approval. Use for tests, git, builds.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "todo_write",
        "description": "Create or update your task checklist for multi-step work. Pass the FULL "
                       "list every time (it replaces the previous one). Plan before you start, "
                       "then mark items in_progress / completed as you go. Keep exactly one item "
                       "in_progress at a time.",
        "parameters": {"type": "object", "properties": {
            "todos": {"type": "array", "items": {"type": "object", "properties": {
                "content": {"type": "string", "description": "Short task description."},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
            }, "required": ["content", "status"]}}
        }, "required": ["todos"]}}},
]

def system_prompt(root: Path, project_context: str) -> str:
    return (
        "You are Odysseus CLI, a focused local coding agent working in "
        f"{root}. Use the provided tools to inspect and edit code.\n"
        "Rules:\n"
        "- For greetings, simple questions, or general conversation, just reply "
        "directly in plain text. Do NOT read files, plan, or call any tool.\n"
        "- Only use tools when the request actually needs reading, searching, "
        "editing files, or running commands in this project.\n"
        "- For a genuine MULTI-STEP coding task, call todo_write FIRST to plan, "
        "then keep it updated (one item in_progress at a time). Skip planning "
        "for trivial one-step tasks.\n"
        "- Use read_file / list_dir / grep to understand the code BEFORE editing.\n"
        "- Make minimal, targeted edits with edit_file; use write_file for new files.\n"
        "- Never call the same tool with the same arguments twice — use the result you got.\n"
        "- When the task is done, STOP calling tools and give a short plain-text summary.\n"
        "- Prefer paths relative to the project root.\n\n"
        + project_context
    )


# ── Tool implementations ───────────────────────────────────────────────────
def _read_file(root: Path, path: str) -> str:
    p = sandbox.resolve_in_root(path, root)
    if p is None:
        return f"Error: '{path}' is outside the project root."
    if not p.is_file():
        return f"Error: file not found: {path}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Error reading {path}: {exc}"
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n… (truncated, {len(text)} chars total)"
    return text or "(empty file)"


def _list_dir(root: Path, path: str = ".") -> str:
    p = sandbox.resolve_in_root(path or ".", root)
    if p is None:
        return f"Error: '{path}' is outside the project root."
    if not p.is_dir():
        return f"Error: not a directory: {path}"
    entries = []
    for child in sorted(p.iterdir()):
        if child.name.startswith("."):
            continue
        entries.append(child.name + ("/" if child.is_dir() else ""))
    return "\n".join(entries) or "(empty directory)"


def _grep(root: Path, pattern: str, path: str = ".") -> str:
    import re
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    base = sandbox.resolve_in_root(path or ".", root)
    if base is None:
        return f"Error: '{path}' is outside the project root."
    files = [base] if base.is_file() else [
        f for f in base.rglob("*")
        if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
    ]
    hits = []
    for f in files[:500]:
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    rel = f.relative_to(root)
                    hits.append(f"{rel}:{i}: {line.strip()[:160]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n… (more matches truncated)"
        except Exception:
            continue
    return "\n".join(hits) or "(no matches)"


async def _approve(state: ApprovalState, tool: str, preview: str, diff_lines=None) -> bool:
    if state.policy == APPROVAL_AUTO or state.always_allowed(tool):
        return True
    if state.policy == APPROVAL_DENY:
        r.info(f"{tool} blocked (read-only mode)")
        return False
    decision = await _approval._prompt(tool, preview, diff_lines=diff_lines)
    if decision == "always":
        state.grant_always(tool)
        return True
    return decision == "yes"


async def _write_file(root: Path, state: ApprovalState, path: str, content: str) -> str:
    resolved = sandbox.resolve_in_root(path, root)
    if resolved is None:
        return f"Error: '{path}' is outside the project root."
    diff = sandbox.unified_diff_for_write(resolved, content)
    if not await _approve(state, "write_file", path, diff):
        return "User declined the write. Choose another approach or ask the user."
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing {path}: {exc}"
    return f"Wrote {len(content)} bytes to {path}."


async def _edit_file(root: Path, state: ApprovalState, path: str, find: str, replace: str) -> str:
    resolved = sandbox.resolve_in_root(path, root)
    if resolved is None:
        return f"Error: '{path}' is outside the project root."
    if not resolved.is_file():
        return f"Error: file not found: {path}"
    original = resolved.read_text(encoding="utf-8", errors="replace")
    if find not in original:
        return f"Error: the 'find' text was not found in {path}. Read the file and match exactly."
    if original.count(find) > 1:
        return f"Error: 'find' text appears {original.count(find)} times in {path}; make it unique."
    updated = original.replace(find, replace, 1)
    diff = sandbox.unified_diff_for_write(resolved, updated)
    if not await _approve(state, "edit_file", path, diff):
        return "User declined the edit. Choose another approach or ask the user."
    try:
        resolved.write_text(updated, encoding="utf-8")
    except Exception as exc:
        return f"Error editing {path}: {exc}"
    return f"Edited {path}."


async def _bash(root: Path, state: ApprovalState, command: str) -> str:
    if not await _approve(state, "bash", command):
        return "User declined the command. Choose another approach or ask the user."
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 120s."
    except Exception as exc:
        return f"Error running command: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    out = out.strip() or "(no output)"
    return f"exit={proc.returncode}\n{out[:MAX_TOOL_OUTPUT]}"


def _todo_write(state: ApprovalState, todos: list) -> str:
    """Replace the agent's task checklist and render it."""
    clean = []
    for t in todos or []:
        if isinstance(t, dict) and t.get("content"):
            clean.append({
                "content": str(t.get("content")),
                "status": str(t.get("status") or "pending"),
            })
    state.todos = clean
    r.todos(clean)
    done = sum(1 for t in clean if t["status"] == "completed")
    return f"Plan updated: {len(clean)} task(s), {done} completed."


async def _dispatch(cfg: CliConfig, state: ApprovalState, name: str, args: dict) -> str:
    root = cfg.project_root
    if name == "todo_write":
        return _todo_write(state, args.get("todos", []))
    if name == "read_file":
        return _read_file(root, args.get("path", ""))
    if name == "list_dir":
        return _list_dir(root, args.get("path", "."))
    if name == "grep":
        return _grep(root, args.get("pattern", ""), args.get("path", "."))
    if name == "write_file":
        return await _write_file(root, state, args.get("path", ""), args.get("content", ""))
    if name == "edit_file":
        return await _edit_file(root, state, args.get("path", ""),
                                args.get("find", ""), args.get("replace", ""))
    if name == "bash":
        return await _bash(root, state, args.get("command", ""))
    return f"Error: unknown tool '{name}'."


def _headers(cfg: CliConfig) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if cfg.api_key:
        h["Authorization"] = f"Bearer {cfg.api_key}"
    return h


def _calls_from_message(msg: dict):
    """Return [(name, args)] from a model message.

    Prefers native tool_calls; falls back to parsing JSON tool calls out of the
    text content (Ollama does not lift qwen/deepseek tool calls into the native
    field — they arrive as JSON in `content`).
    """
    out = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {}) or {}
        name = fn.get("name", "")
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}
        if name:
            out.append((name, args))
    if out:
        return out
    # Fallback: parse the text content for JSON tool calls.
    from . import toolcompat
    return toolcompat.extract_tool_calls(msg.get("content") or "")


async def run_turn(cfg: CliConfig, messages: List[Dict], state: ApprovalState) -> str:
    """Run one user turn through the native tool-calling loop.

    Mutates `messages` in place; returns the final assistant text.
    """
    url = to_chat_completions_url(cfg.endpoint)
    r.assistant_prefix(cfg.model)
    final_text = ""
    seen_calls: Dict = {}
    force_answer = False

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        for round_num in range(1, cfg.max_rounds + 1):
            payload = {
                "model": cfg.model,
                "messages": messages,
                "temperature": cfg.temperature,
                "stream": False,
            }
            if not force_answer:
                payload["tools"] = TOOLS
            try:
                resp = await client.post(url, json=payload, headers=_headers(cfg))
            except Exception as exc:
                r.error(f"request failed: {exc}")
                return final_text
            if resp.status_code != 200:
                r.error(f"{resp.status_code}: {resp.text[:300]}")
                return final_text

            data = resp.json()
            usage = data.get("usage") or {}
            if usage:
                state.last_usage = usage
            msg = data.get("choices", [{}])[0].get("message", {}) or {}
            content = (msg.get("content") or "").strip()
            calls = [] if force_answer else _calls_from_message(msg)

            # No tool calls (or we forced a no-tools round) → this is the answer.
            if not calls:
                if content:
                    r.delta(content)
                    r.write()
                    final_text = content
                messages.append({"role": "assistant", "content": content})
                return final_text

            # Filter out calls we've already run this turn (loop guard).
            fresh = []
            for name, args in calls:
                sig = (name, json.dumps(args, sort_keys=True))
                seen_calls[sig] = seen_calls.get(sig, 0) + 1
                if seen_calls[sig] == 1:
                    fresh.append((name, args))
            if not fresh:
                # Model is repeating itself — force a final, tool-free answer.
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                    "You already have all the tool results you requested above. "
                    "Do not call any tool again. Give your final answer now."})
                force_answer = True
                continue

            # Record the assistant's tool-calling turn, then run each call and
            # feed results back as clear messages the model will read next round.
            messages.append({"role": "assistant", "content": content})
            for name, args in fresh:
                preview = args.get("command") or args.get("path") or json.dumps(args)
                r.tool_start(name, str(preview), round_num)
                result = await _dispatch(cfg, state, name, args)
                r.tool_output(name, result, 0)
                messages.append({"role": "user", "content":
                    f"[tool result: {name}]\n{result[:MAX_TOOL_OUTPUT]}"})

    r.info("(max rounds reached)")
    return final_text


async def compact(cfg: CliConfig, messages: List[Dict]) -> List[Dict]:
    """Summarize the conversation so far to free context.

    Keeps the system message, replaces everything else with a concise summary of
    facts, decisions, files touched, and the current task state. Returns the new
    message list (or the original on failure).
    """
    if len(messages) <= 2:
        return messages
    system = messages[0]
    transcript_parts = []
    for m in messages[1:]:
        role = m.get("role", "?")
        content = (m.get("content") or "")[:2000]
        if content:
            transcript_parts.append(f"{role}: {content}")
    transcript = "\n".join(transcript_parts)

    prompt = [
        {"role": "system", "content":
            "Summarize the following coding-session transcript for handoff. Be "
            "concise but preserve: the user's goal, key facts learned, files "
            "read/edited, decisions made, and what remains to do. Output only the "
            "summary."},
        {"role": "user", "content": transcript[:20000]},
    ]
    url = to_chat_completions_url(cfg.endpoint)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, json={
                "model": cfg.model, "messages": prompt,
                "temperature": 0.1, "stream": False,
            }, headers=_headers(cfg))
        if resp.status_code != 200:
            return messages
        summary = (resp.json().get("choices", [{}])[0]
                   .get("message", {}).get("content") or "").strip()
    except Exception:
        return messages
    if not summary:
        return messages
    return [system, {"role": "user",
                     "content": f"[Summary of earlier conversation]\n{summary}"}]
