"""Keep tool output that does not fit in the model context.

The capture may be larger than ``MAX_OUTPUT_CHARS``. The model receives a head
and the path of the full capture, under the agent workspace.
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
    if not isinstance(text, str):
        text = "" if text is None else str(text)
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
