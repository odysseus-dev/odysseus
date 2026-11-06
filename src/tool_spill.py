"""Persist tool output that does not fit in the model context.

A tool result can be much larger than ``MAX_OUTPUT_CHARS``. When it is, the
full capture is written under ``<agent workspace>/spills/`` and the model gets
the head plus the path, so the tail stays reachable without spending context.

The returned path is also exposed to the caller (``spill_path`` in the tool
result), which keeps it usable even when the visible note is cut short.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from src.constants import AGENT_WORKSPACE_DIR, MAX_OUTPUT_CHARS


def spill_tool_output(
    text: str,
    *,
    tool: str,
    session_id: str = "",
    limit: int = MAX_OUTPUT_CHARS,
) -> tuple[str, str | None]:
    """Return ``(visible_text, path_or_none)`` for a tool capture.

    ``visible_text`` never exceeds ``limit``. When the capture fits, it is
    returned untouched with ``None``; otherwise the full text is written to a
    file and the visible text is a head plus a note with that path.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    limit = max(0, int(limit))
    if len(text) <= limit:
        return text, None
    folder = Path(AGENT_WORKSPACE_DIR) / "spills"
    folder.mkdir(parents=True, exist_ok=True)
    safe_tool = "".join(ch if ch.isalnum() else "-" for ch in (tool or "tool"))[:40] or "tool"
    safe_sess = "".join(ch if ch.isalnum() else "-" for ch in (session_id or "nosess"))[:48] or "nosess"
    path = folder / f"{safe_sess}-{safe_tool}-{uuid.uuid4().hex[:10]}.txt"
    path.write_text(text, encoding="utf-8")
    note = f"\n…[full output in {path} ({len(text)} chars)]"
    head_budget = max(0, limit - len(note))
    shown = text[:head_budget] + note
    if len(shown) > limit:
        shown = note if len(note) <= limit else note[:limit]
    return shown, str(path)
