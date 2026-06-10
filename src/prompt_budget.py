"""Prompt budget accounting helpers.

These helpers intentionally return counts and labels only. They accept prompt
text long enough to estimate its size, but the report never includes the text
itself so diagnostics can be logged or displayed without leaking user content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from src.model_context import estimate_tokens


@dataclass(frozen=True)
class PromptBudgetSection:
    name: str
    category: str
    text: str = ""
    item_count: Optional[int] = None
    join_before: str = "\n\n"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # estimate_tokens includes per-message framing overhead. A prompt section
    # is not necessarily its own API message, so subtract that fixed wrapper
    # and keep only the content estimate.
    return max(0, estimate_tokens([{"role": "system", "content": text}]) - 4)


def build_prompt_budget_report(
    sections: Iterable[PromptBudgetSection],
    *,
    largest_limit: int = 10,
) -> Dict[str, Any]:
    """Return section-level prompt costs without returning raw prompt text."""
    rows: List[Dict[str, Any]] = []
    for section in sections:
        text = _as_text(section.text)
        row: Dict[str, Any] = {
            "name": section.name,
            "category": section.category,
            "char_count": len(text),
            "estimated_tokens": _estimate_text_tokens(text),
        }
        if section.item_count is not None:
            row["item_count"] = section.item_count
        rows.append(row)

    total_chars = sum(row["char_count"] for row in rows)
    total_tokens = sum(row["estimated_tokens"] for row in rows)
    for row in rows:
        row["percent_of_total"] = (
            round((row["estimated_tokens"] / total_tokens) * 100, 2)
            if total_tokens
            else 0.0
        )

    largest = sorted(
        rows,
        key=lambda row: (
            -row["estimated_tokens"],
            -row["char_count"],
            row["category"],
            row["name"],
        ),
    )[: max(0, largest_limit)]

    return {
        "total": {
            "char_count": total_chars,
            "estimated_tokens": total_tokens,
        },
        "sections": rows,
        "largest": [dict(row) for row in largest],
    }


def prompt_message_sections(messages: Iterable[Dict[str, Any]]) -> List[PromptBudgetSection]:
    """Classify already-assembled LLM messages for prompt diagnostics."""
    sections: List[PromptBudgetSection] = []
    for idx, message in enumerate(messages):
        content = message.get("content", "")
        metadata = message.get("metadata") or {}
        source = str(metadata.get("source") or "").strip().lower()
        role = str(message.get("role") or "message")

        if metadata.get("trusted") is False:
            if "memory" in source or "memories" in source:
                category = "memory_context"
            elif "skill" in source:
                category = "skill_context"
            elif "document" in source or "editor" in source:
                category = "document_context"
            else:
                category = "untrusted_context"
            name_source = source.replace(" ", "_") or category
            name = f"{category}.{name_source}.{idx}"
        else:
            category = f"{role}_message"
            name = f"{category}.{idx}"

        sections.append(PromptBudgetSection(name=name, category=category, text=_as_text(content)))
    return sections
