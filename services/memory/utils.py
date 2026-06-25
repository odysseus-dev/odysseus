"""Shared utilities for memory service modules."""

from __future__ import annotations

from typing import Any, Dict, List


def strip_media(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove non-text content (images, audio) from messages.

    Background extraction only needs text. VL-generated descriptions are
    already in the text content. This avoids sending image tokens to
    non-vision models and prevents accidental vision grounding triggers.

    Messages with no text content after filtering are dropped entirely.
    """
    stripped = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            text_only = [
                b for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if not text_only and content:
                continue
            content = text_only
        stripped.append({"role": msg.get("role"), "content": content})
    return stripped


def flatten_message_text(message: Dict[str, Any]) -> str:
    """Extract plain text from a message, joining list content blocks."""
    content = message.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return content
