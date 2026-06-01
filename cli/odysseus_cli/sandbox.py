"""Project sandboxing + diff preview for file tools.

The agent's ``read_file`` / ``write_file`` tools take a path on the first line of
the tool block (see src/tool_execution.py:_parse_write_file). This module:

  * resolves those paths against the project root and rejects any that escape it
    (path-containment sandbox), and
  * builds a unified diff for ``write_file`` so the approval prompt can show
    exactly what will change before the file is written.

``bash`` / ``python`` are NOT path-sandboxed here — a shell can always escape a
path check — so the approval prompt is their control instead.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import List, Optional, Tuple

# Tools whose first content line is a filesystem path we can contain.
PATH_TOOLS = {"read_file", "write_file"}


def tool_path(tool: str, content: str) -> Optional[str]:
    """Extract the target path from a file tool block, if any."""
    if tool not in PATH_TOOLS:
        return None
    first = (content or "").split("\n", 1)[0].strip()
    return first or None


def resolve_in_root(path_str: str, root: Path) -> Optional[Path]:
    """Resolve path_str against root; return the path only if it stays inside.

    Returns None when the path escapes the project root (absolute outside, or
    via ``..`` traversal) — the caller should treat None as "deny".
    """
    root = Path(root).resolve()
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    try:
        rp = p.resolve()
    except Exception:
        return None
    if rp == root or root in rp.parents:
        return rp
    return None


def split_write(content: str) -> Tuple[str, str]:
    """Mirror src/tool_execution._parse_write_file: (path, new_content)."""
    parts = (content or "").split("\n", 1)
    return parts[0].strip(), (parts[1] if len(parts) > 1 else "")


def unified_diff_for_write(target: Path, new_content: str) -> List[str]:
    """Build a unified diff (list of lines) between target's current content
    and the proposed new content. New files diff against an empty file."""
    old_text = ""
    if target.is_file():
        try:
            old_text = target.read_text(encoding="utf-8", errors="replace")
        except Exception:
            old_text = ""
    label = target.name or str(target)
    diff = difflib.unified_diff(
        old_text.splitlines(),
        new_content.splitlines(),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        lineterm="",
    )
    return list(diff)
