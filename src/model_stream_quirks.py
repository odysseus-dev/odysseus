"""Thinking-without-action resiliency for reasoning models.

Universal defaults apply to any model that closes a thinking block without
emitting tools or a visible reply. MODEL_STREAM_QUIRKS holds optional
per-model timing overrides only.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Dict, Optional, Tuple, TypedDict

# Minimum visible reply chars after thinking closes before we treat the stream
# as healthy (mirrors chat.js anti-stall threshold).
_MIN_REPLY_AFTER_THINKING_CHARS = 24

# Post-</thinking> silence before a silent auto-nudge (ms). Local 8–14B models
# usually emit the first reply/tool token within ~3–8s; stalls produce nothing.
THINKING_ONLY_NUDGE_MS = 12_000

# Hard timeout after reasoning closes with no tool call and almost no reply.
THINKING_ONLY_TIMEOUT_MS = 25_000

# Back-compat alias for older imports/tests.
DEFAULT_THINKING_ONLY_STALL_MS = THINKING_ONLY_NUDGE_MS


class ModelStreamQuirk(TypedDict, total=False):
    thinking_only_nudge_ms: int
    thinking_only_timeout_ms: int
    thinking_only_stall_ms: int  # legacy alias for nudge_ms
    auto_continue_on_thinking_only: bool


# Optional per-model overrides — empty by default (universal policy).
MODEL_STREAM_QUIRKS: Dict[str, ModelStreamQuirk] = {}

# Tool names mentioned inside thinking prose without fenced/native calls.
_TOOL_INTENT_IN_THINKING_RE = re.compile(
    r"(?:"
    r"\b(?:should|will|need to|going to|i['']?ll|i will)\s+"
    r"(?:call|use|invoke|run)\s+(?:the\s+)?(?:tool\s+)?[`'\"]?([a-z][a-z0-9_]{2,})[`'\"]?"
    r"|"
    r"\b(?:call|invoke|use)\s+(?:the\s+)?(?:tool\s+)?[`'\"]?([a-z][a-z0-9_]{2,})[`'\"]?"
    r")",
    re.IGNORECASE,
)


def _normalize_model(model: str) -> str:
    return (model or "").strip().lower()


def match_model_stream_quirk(model: str) -> Optional[Tuple[str, ModelStreamQuirk]]:
    """Return ``(pattern, quirk)`` for the most specific matching override."""
    name = _normalize_model(model)
    if not name:
        return None
    best_pattern: Optional[str] = None
    best_quirk: Optional[ModelStreamQuirk] = None
    for pattern, quirk in MODEL_STREAM_QUIRKS.items():
        pat = pattern.lower()
        if fnmatch.fnmatch(name, pat):
            if best_pattern is None or len(pat) > len(best_pattern):
                best_pattern = pattern
                best_quirk = quirk
    if best_quirk is None:
        return None
    return best_pattern, best_quirk


def get_model_stream_quirk(model: str) -> Optional[ModelStreamQuirk]:
    matched = match_model_stream_quirk(model)
    return matched[1] if matched else None


def resolve_thinking_stall_policy(model: str) -> Dict[str, object]:
    """Return nudge/timeout policy for any model (defaults or override)."""
    quirk = get_model_stream_quirk(model) or {}
    return {
        "nudge_ms": (
            quirk.get("thinking_only_nudge_ms")
            or quirk.get("thinking_only_stall_ms")
            or THINKING_ONLY_NUDGE_MS
        ),
        "timeout_ms": quirk.get("thinking_only_timeout_ms") or THINKING_ONLY_TIMEOUT_MS,
        "auto_continue_on_thinking_only": quirk.get("auto_continue_on_thinking_only", True),
    }


def thinking_tool_intent_in_text(text: str) -> Optional[str]:
    """Return a mentioned tool name when thinking prose describes a call."""
    if not text:
        return None
    match = _TOOL_INTENT_IN_THINKING_RE.search(text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return group
    return None


def extract_thinking_blocks(text: str) -> str:
    """Join all ``<think>`` blocks from a round response."""
    blocks = re.findall(
        r"<think>(.*?)</think>",
        text or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    return "\n".join(blocks)


def quirk_thinking_intent(round_response: str, model: str) -> Optional[str]:
    """Detect tool intent buried in thinking blocks (any reasoning model)."""
    thinking = extract_thinking_blocks(round_response)
    if not thinking:
        return None
    return thinking_tool_intent_in_text(thinking)
