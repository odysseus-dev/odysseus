"""Attachment reference helpers for chat storage and tool manifests.

Live model calls may need provider-specific data URLs for the current turn.
Persisted history and search indexes should keep stable upload references and
human-readable text instead of duplicating raw media bytes.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable


DATA_URL_RE = re.compile(
    r"data:[^;,\s\"']+;base64,[A-Za-z0-9+/=]+",
    re.IGNORECASE,
)

MEDIA_BLOCK_TYPES = {
    "image",
    "image_url",
    "input_image",
    "audio",
    "input_audio",
    "file",
}


def strip_inline_data_urls(text: str) -> str:
    """Replace inline data URLs with a compact marker."""
    if not isinstance(text, str) or ";base64," not in text:
        return text
    return DATA_URL_RE.sub("[inline media omitted from persisted history]", text)


def attachment_ref(info: dict[str, Any]) -> dict[str, Any]:
    """Return the stable attachment reference shape used outside raw uploads."""
    upload_id = str(info.get("id") or info.get("attachment_id") or "").strip()
    try:
        size = int(info.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    ref = {
        "type": "attachment_ref",
        "attachment_id": upload_id,
        "name": info.get("name") or info.get("original_name") or upload_id,
        "mime": info.get("mime") or "application/octet-stream",
        "size": size,
    }
    checksum = info.get("checksum_sha256") or info.get("sha256") or info.get("hash")
    if checksum:
        ref["checksum_sha256"] = checksum
    created_at = info.get("created_at") or info.get("uploaded_at")
    if created_at:
        ref["created_at"] = created_at
    for key in ("width", "height", "vision", "vision_model", "gallery_id"):
        value = info.get(key)
        if value is not None:
            ref[key] = value
    return ref


def attachment_refs_from_metadata(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract attachment refs from message metadata."""
    attachments = (metadata or {}).get("attachments") or []
    if not isinstance(attachments, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in attachments:
        if isinstance(item, dict):
            ref = attachment_ref(item)
            if ref.get("attachment_id"):
                refs.append(ref)
    return refs


def _ref_line(ref: dict[str, Any]) -> str:
    parts = [f"Attachment: {ref.get('name') or ref.get('attachment_id') or 'upload'}"]
    if ref.get("attachment_id"):
        parts.append(f"id={ref['attachment_id']}")
    if ref.get("mime"):
        parts.append(f"mime={ref['mime']}")
    if ref.get("size"):
        parts.append(f"size={ref['size']} bytes")
    if ref.get("checksum_sha256"):
        parts.append(f"sha256={ref['checksum_sha256']}")
    line = "[" + " | ".join(parts) + "]"
    if ref.get("vision"):
        line += f"\n[Attachment description: {str(ref['vision']).strip()}]"
    return line


def _text_from_blocks(blocks: Iterable[Any]) -> str:
    lines: list[str] = []
    omitted_media = 0
    for block in blocks:
        if isinstance(block, str):
            lines.append(strip_inline_data_urls(block))
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                lines.append(strip_inline_data_urls(text))
        elif block_type == "attachment_ref":
            lines.append(_ref_line(block))
        elif block_type in MEDIA_BLOCK_TYPES:
            omitted_media += 1
        else:
            try:
                encoded = json.dumps(block, ensure_ascii=True, sort_keys=True)
            except TypeError:
                encoded = str(block)
            lines.append(strip_inline_data_urls(encoded))
    if omitted_media:
        plural = "s" if omitted_media != 1 else ""
        lines.append(f"[{omitted_media} inline media payload{plural} omitted]")
    return "\n".join(line for line in lines if line).strip()


def persistable_message_content(
    content: Any,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Return content safe for DB persistence and FTS indexing.

    Multimodal provider blocks are collapsed to readable text plus stable
    attachment reference lines from metadata. This avoids storing base64 media
    in ``chat_messages.content`` while preserving enough context for reloads,
    search, and later turns.
    """
    if isinstance(content, list):
        text = _text_from_blocks(content)
        refs = attachment_refs_from_metadata(metadata)
        ref_lines = [_ref_line(ref) for ref in refs]
        if ref_lines:
            text = "\n".join([part for part in (text, "\n".join(ref_lines)) if part]).strip()
        return text
    if isinstance(content, str):
        return strip_inline_data_urls(content)
    try:
        return strip_inline_data_urls(json.dumps(content, ensure_ascii=True, sort_keys=True))
    except TypeError:
        return strip_inline_data_urls(str(content))


def search_index_text(content: Any) -> str:
    """Best-effort searchable text for legacy stored content."""
    if isinstance(content, str):
        raw = content.strip()
        if raw.startswith("[") and '"type"' in raw:
            try:
                parsed = json.loads(content)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return _text_from_blocks(parsed)
        return strip_inline_data_urls(content)
    if isinstance(content, list):
        return _text_from_blocks(content)
    return persistable_message_content(content)
