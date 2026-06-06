"""Model-specific streaming / agent-loop quirks for local models with bad UX.

Keyed by fnmatch patterns (e.g. ``gemma4:e4b``, ``gemma4:*``). Prefer the
longest matching pattern when multiple entries apply.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Dict, Optional, Tuple, TypedDict

# Minimum visible reply chars after thinking closes before we treat the stream
# as healthy (mirrors chat.js anti-stall threshold).
_MIN_REPLY_AFTER_THINKING_CHARS = 24

# Default post-thinking silence before surfacing a stall prompt (ms).
DEFAULT_THINKING_ONLY_STALL_MS = 15_000


class ModelStreamQuirk(TypedDict, total=False):
    thinking_only_stall_ms: int
    auto_continue_on_thinking_only: bool


MODEL_STREAM_QUIRKS: Dict[str, ModelStreamQuirk] = {
    "gemma4:e4b": {
        "thinking_only_stall_ms": DEFAULT_THINKING_ONLY_STALL_MS,
        "auto_continue_on_thinking_only": True,
    },
    "gemma4:*": {
        "thinking_only_stall_ms": DEFAULT_THINKING_ONLY_STALL_MS,
        "auto_continue_on_thinking_only": True,
    },
}

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
    """Return ``(pattern, quirk)`` for the most specific matching entry."""
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
    """Detect tool intent buried in thinking for quirk-registered models only."""
    if not get_model_stream_quirk(model):
        return None
    thinking = extract_thinking_blocks(round_response)
    return thinking_tool_intent_in_text(thinking)
