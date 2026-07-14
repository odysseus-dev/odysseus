"""Fallback UI card when chat wizard stalls (LLM never confirms)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from titan.image_pipeline_config import fallback_card_enabled, should_use_wizard
from titan.image_proposal import build_proposal, load_source_provenance


def _parse_tool_args(command: str) -> Dict[str, Any]:
    if not command:
        return {}
    text = command.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    try:
        from titan.image_parse import parse_generate_image

        return parse_generate_image(text)
    except Exception:
        return {}


def _has_image_in_events(events: List[Dict[str, Any]], after_index: int = 0) -> bool:
    for ev in events[after_index:]:
        if ev.get("image_url"):
            return True
        out = str(ev.get("output") or "")
        if "Generated image for:" in out and "/api/generated-image/" in out:
            return True
    return False


def fallback_proposal_from_tool_events(
    tool_events: List[Dict[str, Any]],
    *,
    owner: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """If wizard confirm is pending and no image ran, build a card proposal."""
    if not fallback_card_enabled() or not tool_events:
        return None

    last_idx = -1
    last_ev: Optional[Dict[str, Any]] = None
    for i, ev in enumerate(tool_events):
        if ev.get("tool") != "generate_image":
            continue
        last_idx = i
        last_ev = ev

    if last_ev is None:
        return None

    out = str(last_ev.get("output") or "")
    is_wizard_pending = (
        last_ev.get("wizard_pending")
        or last_ev.get("pending_user")
        and "NEEDS_USER_INPUT" in out
        and "confirm" in out.lower()
    )
    if not is_wizard_pending:
        return None

    args = _parse_tool_args(str(last_ev.get("command") or ""))
    op = str(args.get("op") or "generate").strip().lower()
    if not should_use_wizard(op):
        return None

    if _has_image_in_events(tool_events, after_index=last_idx + 1):
        return None
    if last_ev.get("image_url"):
        return None

    source_id = (args.get("source_image_id") or "").strip() or None
    src = load_source_provenance(source_id, owner) if source_id else None
    proposal = build_proposal(args, source_provenance=src)
    proposal["fallback"] = True
    return proposal
