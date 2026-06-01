"""Tool-call compatibility shim for local Hermes/Qwen/DeepSeek-style models.

Odysseus converts a model's tool intent to execution in two ways:
  * native OpenAI `tool_calls` returned by the endpoint, or
  * fenced code blocks (```bash, ```read_file …) parsed from the text.

Local coder models (Qwen2.5-Coder, DeepSeek, Hermes fine-tunes) served via
Ollama frequently do *neither*: they print the call as JSON in the text —
``{"name": "read_file", "arguments": {"file_path": "x"}}`` — sometimes wrapped
in a ```json fence or ``<tool_call>`` tags. Ollama's template doesn't lift that
into native `tool_calls`, and the fenced-block parser doesn't recognize it, so
the call silently never executes.

This module teaches the parser that format. It wraps ``parse_tool_blocks`` so
that, when normal parsing finds nothing, JSON tool calls in the text are
extracted, their argument names normalized to what Odysseus expects, and
converted into ToolBlocks via the existing ``function_call_to_tool_block``.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Tuple

# <tool_call>{...}</tool_call>  (Qwen / Hermes native wrapper)
_TAG_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
# ```json ... ```  /  ```tool_call ... ```  fenced object
_FENCE_RE = re.compile(r"```(?:json|tool_call|tool_code)?\s*(\{.*?\})\s*```", re.DOTALL)

# Per-tool argument aliases → the key function_call_to_tool_block expects.
_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "read_file":  {"file_path": "path", "filepath": "path", "filename": "path", "file": "path"},
    "write_file": {"file_path": "path", "filepath": "path", "filename": "path", "file": "path"},
    "bash":       {"cmd": "command", "script": "command", "shell": "command"},
    "python":     {"script": "code", "source": "code"},
    "web_search": {"q": "query", "search_query": "query"},
}


def _balanced_objects(text: str) -> List[str]:
    """Yield top-level {...} substrings via brace matching (ignores nesting)."""
    out, depth, start = [], 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    out.append(text[start:i + 1])
                    start = -1
    return out


def _normalize_args(name: str, args: dict) -> dict:
    aliases = _ARG_ALIASES.get(name, {})
    if not aliases:
        return args
    out = dict(args)
    for alias, canonical in aliases.items():
        if alias in out and canonical not in out:
            out[canonical] = out.pop(alias)
    return out


def extract_tool_calls(text: str) -> List[Tuple[str, dict]]:
    """Find JSON tool calls in model text. Returns [(name, normalized_args)]."""
    if not text or ("name" not in text):
        return []
    candidates: List[str] = []
    candidates += _TAG_RE.findall(text)
    candidates += _FENCE_RE.findall(text)
    candidates += _balanced_objects(text)

    calls: List[Tuple[str, dict]] = []
    seen = set()
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name") or obj.get("tool") or obj.get("function")
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters")
        if not isinstance(name, str) or not isinstance(args, (dict, type(None))):
            continue
        args = _normalize_args(name, args or {})
        key = (name, json.dumps(args, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        calls.append((name, args))
    return calls


def install() -> None:
    """Wrap parse_tool_blocks so JSON tool calls become executable ToolBlocks."""
    import src.agent_loop as al
    from src.agent_tools import function_call_to_tool_block

    original = al.parse_tool_blocks

    def patched(text):
        blocks = original(text)
        if blocks:
            return blocks
        recovered = []
        for name, args in extract_tool_calls(text or ""):
            tb = function_call_to_tool_block(name, json.dumps(args))
            if tb:
                recovered.append(tb)
        return recovered

    al.parse_tool_blocks = patched
