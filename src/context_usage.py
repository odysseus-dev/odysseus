"""Categorized context-window token estimates for agent turns.

This module splits the final message array into coarse token buckets so the
frontend can show a Cursor-style expandable breakdown.  All counts are
estimates using the same chars*0.3 heuristic as :func:`estimate_tokens`.
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.model_context import estimate_text_tokens

# Order used when returning categories to the frontend.
_CATEGORY_ORDER = [
    "system_prompt",
    "tool_definitions",
    "rules",
    "skills",
    "mcp",
    "memory",
    "documents",
    "web",
    "summarized_conversation",
    "conversation",
]

_CATEGORY_LABELS = {
    "system_prompt": "System prompt",
    "tool_definitions": "Tool definitions",
    "rules": "Rules",
    "skills": "Skills",
    "mcp": "MCP",
    "memory": "Memory",
    "documents": "Documents",
    "web": "Web",
    "summarized_conversation": "Summarized conversation",
    "conversation": "Conversation",
}

# Marker that _build_base_prompt uses to introduce in-prompt MCP descriptions.
_MCP_IN_PROMPT_MARKER = "You also have access to external MCP tool servers."

# Marker prefixes for untrusted context sources.
_SOURCE_PATTERNS = {
    "skills": ["skills"],
    "memory": ["saved memory:"],
    "documents": ["active editor document", "retrieved documents"],
    "web": [
        "web search results",
        "web page:",
        "prefetched search context",
        "youtube transcript",
    ],
}


def _message_text_content(msg: Dict[str, Any]) -> str:
    """Return the textual content of a message for classification."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return ""


def _message_content_tokens(msg: Dict[str, Any]) -> int:
    """Token count for a message's content and native tool_calls (no overhead)."""
    total = 0
    content = msg.get("content", "")
    if isinstance(content, str):
        total += estimate_text_tokens(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                total += estimate_text_tokens(item.get("text", ""))
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            name = fn.get("name", "") or ""
            args = fn.get("arguments", "") or ""
            if not isinstance(args, str):
                args = str(args)
            total += 4  # per tool-call overhead
            total += estimate_text_tokens(str(name) + args)
    return total


def _message_tokens(msg: Dict[str, Any]) -> int:
    """Total token estimate for a message including per-message overhead."""
    return 4 + _message_content_tokens(msg)


def _classify_source_message(msg: Dict[str, Any]) -> Optional[str]:
    """Classify user-role context messages by metadata.source."""
    meta = msg.get("metadata") or {}
    source = str(meta.get("source") or "").lower().strip()
    if not source:
        return None
    for category, patterns in _SOURCE_PATTERNS.items():
        if any(pattern in source for pattern in patterns):
            return category
    return None


def _is_summarized_message(msg: Dict[str, Any]) -> bool:
    """Detect compaction/summary messages."""
    meta = msg.get("metadata") or {}
    if meta.get("compacted"):
        return True
    text = _message_text_content(msg).strip()
    return text.startswith("[Conversation summary")


def _split_agent_system(content: str) -> Dict[str, str]:
    """Heuristically split the agent system prompt into coarse segments.

    The agent system blob is built by :func:`_build_base_prompt` and looks like:

        preamble
        ```tool fences``` blocks
        ## Additional tools
        ... one-liner tool entries ...
        (Other tools available when needed: ...)
        ## Rules / ## Base rules
        ... rules + domain rules ...
        integration prompt
        You also have access to external MCP tool servers.
        ... MCP descriptions ...

    Returns a dict of ``segment -> text``.  Empty strings mean the segment was
    not detected.
    """
    segments: Dict[str, str] = {
        "system_prompt": "",
        "tool_definitions": "",
        "rules": "",
        "mcp": "",
    }
    if not content:
        return segments

    # 1. Strip the in-prompt MCP block first so it doesn't leak into rules.
    mcp_idx = content.find(_MCP_IN_PROMPT_MARKER)
    if mcp_idx != -1:
        segments["mcp"] = content[mcp_idx:]
        content = content[:mcp_idx]

    # 2. Rules: from the first rules heading to the end of the remaining blob.
    rules_marker: Optional[str] = None
    for marker in ("## Rules", "## Base rules"):
        idx = content.find(marker)
        if idx != -1:
            rules_marker = marker
            rules_idx = idx
            break
    else:
        rules_idx = -1
    if rules_idx != -1:
        segments["rules"] = content[rules_idx:]
        content = content[:rules_idx]

    # 3. Tool definitions: from the first fenced code block through the
    #    "## Additional tools" section (if present).
    tool_start = content.find("```")
    if tool_start != -1:
        add_start = content.find("## Additional tools")
        if add_start != -1:
            # Capture the Additional-tools section up to the next heading.
            after = content[add_start:]
            next_heading = re.search(r"\n## ", after)
            if next_heading:
                tool_end = add_start + next_heading.start()
            else:
                tool_end = len(content)
            segments["tool_definitions"] = content[tool_start:tool_end]
            segments["system_prompt"] = content[:tool_start]
        else:
            segments["tool_definitions"] = content[tool_start:]
            segments["system_prompt"] = content[:tool_start]
    else:
        segments["system_prompt"] = content

    return segments


def _schema_tokens(schemas: List[Dict[str, Any]]) -> int:
    """Estimate tokens for a list of native function schemas as JSON."""
    if not schemas:
        return 0
    try:
        payload = json.dumps(schemas, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        payload = str(schemas)
    # Per-schema overhead for the function wrapper / name / description keys.
    return (4 * len(schemas)) + estimate_text_tokens(payload)


def compute_context_breakdown(
    messages: List[Dict[str, Any]],
    *,
    tool_schemas: Optional[List[Dict[str, Any]]] = None,
    is_agent: bool = True,
) -> Dict[str, Any]:
    """Return a categorized token breakdown of the provided messages.

    Args:
        messages: Final message array sent to the model (post-trim).
        tool_schemas: Native function/tool schemas included on the last round.
        is_agent: Whether this is an agent turn.  When False, the system prompt
            is not split heuristically.

    Returns:
        ``{"estimated": True, "total_tokens": int, "categories": [...]}``
        where each category is ``{"id": str, "label": str, "tokens": int}``,
        sorted descending by tokens and with zero-token categories omitted.
    """
    categories: Dict[str, int] = {cat: 0 for cat in _CATEGORY_ORDER}
    tool_schemas = tool_schemas or []

    system_split_done = False
    for msg in messages:
        role = msg.get("role")

        if role == "system":
            if is_agent and not system_split_done:
                segments = _split_agent_system(_message_text_content(msg))
                # Assign the per-message overhead to the system prompt bucket.
                categories["system_prompt"] += 4
                for seg, text in segments.items():
                    categories[seg] += estimate_text_tokens(text)
                system_split_done = True
            else:
                categories["system_prompt"] += _message_tokens(msg)
            continue

        if _is_summarized_message(msg):
            categories["summarized_conversation"] += _message_tokens(msg)
            continue

        source_cat = _classify_source_message(msg)
        if source_cat:
            categories[source_cat] += _message_tokens(msg)
            continue

        categories["conversation"] += _message_tokens(msg)

    # Native tool schemas: built-in functions -> tool_definitions, MCP -> mcp.
    builtin_schemas = [
        s for s in tool_schemas if not str(s.get("function", {}).get("name", "")).startswith("mcp__")
    ]
    mcp_schemas = [
        s for s in tool_schemas if str(s.get("function", {}).get("name", "")).startswith("mcp__")
    ]
    categories["tool_definitions"] += _schema_tokens(builtin_schemas)
    categories["mcp"] += _schema_tokens(mcp_schemas)

    result_categories = []
    for cat_id in _CATEGORY_ORDER:
        tokens = categories[cat_id]
        if tokens <= 0:
            continue
        result_categories.append(
            {
                "id": cat_id,
                "label": _CATEGORY_LABELS[cat_id],
                "tokens": tokens,
            }
        )

    # Sort descending by tokens; ties keep the declaration order above.
    result_categories.sort(key=lambda c: c["tokens"], reverse=True)

    total_tokens = sum(c["tokens"] for c in result_categories)
    return {
        "estimated": True,
        "total_tokens": total_tokens,
        "categories": result_categories,
    }
