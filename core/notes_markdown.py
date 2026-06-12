"""Shared helpers for the unified, markdown-native Note model.

A note is a single markdown document (`Note.content`). Checklists live *inside*
that markdown as GitHub-style task lines — `- [ ] todo` / `- [x] done` — rather
than in a separate structured column. These helpers are the single source of
truth for how task lines are detected, ordered, toggled, and synthesised, and
are mirrored 1:1 by the JS helpers in static/js/notes.js so that toggle-by-index
means the same thing on both sides.

Kept dependency-free (stdlib only) so it can be imported from the DB migration,
the REST routes, and the agent tool implementation without import cycles.
"""

import re
from typing import Dict, List, Optional, Tuple

# A task line: optional indent, a bullet (-, *, +), a [ ]/[x] box, then text.
# Group 1 = leading whitespace, 2 = box char, 3 = the item text.
# The separator before the text is `[ \t]?` (not `\s?`): these helpers run
# line-by-line so a newline can't be matched here, but keeping it tab/space-only
# mirrors the JS/markdown regexes exactly so task indexing stays identical.
_TASK_RE = re.compile(r"^([ \t]*)[-*+][ \t]+\[([ xX])\][ \t]?(.*)$")

# Two spaces per nesting level when we *emit* task lines.
_INDENT_UNIT = 2


def _indent_level(ws: str) -> int:
    """Leading whitespace -> nesting level (tabs count as one unit each)."""
    width = sum(_INDENT_UNIT if ch == "\t" else 1 for ch in ws)
    return width // _INDENT_UNIT


def parse_task_lines(content: Optional[str]) -> List[Dict]:
    """Return the task lines found in `content`, in document order.

    Each entry is {index, text, done, indent, line} where `index` is the
    0-based ordinal among task lines (what the API/agent toggle by) and `line`
    is the 0-based source line number.
    """
    if not content:
        return []
    out: List[Dict] = []
    for lineno, raw in enumerate(content.splitlines()):
        m = _TASK_RE.match(raw)
        if not m:
            continue
        out.append({
            "index": len(out),
            "text": m.group(3).strip(),
            "done": m.group(2).lower() == "x",
            "indent": _indent_level(m.group(1)),
            "line": lineno,
        })
    return out


def has_tasks(content: Optional[str]) -> bool:
    """True if `content` contains at least one task line."""
    if not content:
        return False
    return any(_TASK_RE.match(line) for line in content.splitlines())


def toggle_task(content: Optional[str], index: int) -> Tuple[str, Dict]:
    """Flip the done state of the `index`-th task line (0-based).

    Returns (new_content, toggled_item). Raises IndexError if there is no task
    line at that ordinal. Everything else about the line — indent, bullet char,
    surrounding text — is preserved.
    """
    text = content or ""
    lines = text.splitlines()
    seen = -1
    for lineno, raw in enumerate(lines):
        m = _TASK_RE.match(raw)
        if not m:
            continue
        seen += 1
        if seen != index:
            continue
        now_done = m.group(2).lower() != "x"
        box = "x" if now_done else " "
        # Rebuild the box in place, leaving the bullet/indent/text untouched.
        lines[lineno] = re.sub(r"\[[ xX]\]", f"[{box}]", raw, count=1)
        item = {
            "index": index,
            "text": m.group(3).strip(),
            "done": now_done,
            "indent": _indent_level(m.group(1)),
            "line": lineno,
        }
        # splitlines() drops a trailing newline; restore it so round-trips of
        # files that ended in "\n" don't silently lose it.
        joined = "\n".join(lines)
        if text.endswith("\n"):
            joined += "\n"
        return joined, item
    raise IndexError(f"no task line at index {index}")


def items_to_markdown(items: Optional[List[Dict]]) -> str:
    """Render legacy structured items ([{text, done, indent}]) as task lines."""
    if not items:
        return ""
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text", "")).strip()
        if not text:
            continue
        indent = " " * (_INDENT_UNIT * int(it.get("indent", 0) or 0))
        box = "x" if it.get("done") else " "
        out.append(f"{indent}- [{box}] {text}")
    return "\n".join(out)


def merge_items_into_content(content: Optional[str], items: Optional[List[Dict]]) -> str:
    """Fold legacy `items` into markdown `content` (content stays canonical).

    Used by the migration and by the write paths that still accept an `items` /
    `checklist_items` array. If `content` already carries task lines we treat it
    as authoritative and leave it untouched; otherwise the items are appended as
    task lines (after a blank line when there is existing body text).
    """
    base = content or ""
    if not items:
        return base
    if has_tasks(base):
        return base
    md = items_to_markdown(items)
    if not md:
        return base
    if base.strip():
        return base.rstrip() + "\n\n" + md
    return md
