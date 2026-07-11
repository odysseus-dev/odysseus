"""Owner-scoped access to chat attachments.

This tool deliberately does not accept filesystem paths.  The model can pass
only an upload id (or its ``odysseus://attachment/`` URI); the upload index is
re-resolved for the current user immediately before bytes are read.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
from typing import Any, Optional

from src.constants import MAX_READ_CHARS
from src.upload_handler import is_valid_upload_id


logger = logging.getLogger(__name__)

_ATTACHMENT_URI_PREFIX = "odysseus://attachment/"
_TRUNCATION_NOTICE = "\n... [attachment content truncated]"


def parse_attachment_id(content: Any) -> Optional[str]:
    """Return an upload id only for the tool's two exact reference forms.

    JSON is accepted because native function calls arrive as an argument
    object.  The selected value must still be either a canonical upload id or
    the exact internal attachment URI.  Paths, URLs with query strings or
    fragments, and other URL schemes all fail closed.
    """
    value: Any = content
    if isinstance(content, str):
        value = content.strip()
        if value.startswith("{"):
            try:
                args = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            if not isinstance(args, dict):
                return None
            value = (
                args.get("attachment")
                or args.get("attachment_id")
                or args.get("id")
                or args.get("uri")
            )
    elif isinstance(content, dict):
        value = (
            content.get("attachment")
            or content.get("attachment_id")
            or content.get("id")
            or content.get("uri")
        )

    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith(_ATTACHMENT_URI_PREFIX):
        value = value[len(_ATTACHMENT_URI_PREFIX):]
    return value if is_valid_upload_id(value) else None


def _cap_output(text: Any) -> str:
    text = text if isinstance(text, str) else str(text or "")
    if len(text) <= MAX_READ_CHARS:
        return text
    keep = max(0, MAX_READ_CHARS - len(_TRUNCATION_NOTICE))
    return text[:keep] + _TRUNCATION_NOTICE


def _safe_display_name(info: dict, upload_id: str) -> str:
    raw = str(info.get("original_name") or info.get("name") or upload_id)
    # Treat either separator as a separator on every host so an untrusted
    # original filename can never echo an absolute client/server path.
    name = os.path.basename(raw.replace("\\", "/")) or upload_id
    return name[:255]


def _read_text(path: str) -> str:
    # Four bytes per output character covers UTF-8 while bounding bytes read.
    with open(path, "rb") as handle:
        raw = handle.read((MAX_READ_CHARS * 4) + 1)
    return raw.decode("utf-8", errors="replace")


def _read_pdf(path: str) -> str:
    from pypdf import PdfReader

    chunks: list[str] = []
    length = 0
    for page_number, page in enumerate(PdfReader(path).pages, 1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue
        chunk = f"[Page {page_number}]\n{page_text}"
        chunks.append(chunk)
        length += len(chunk) + 2
        if length > MAX_READ_CHARS:
            break
    return "\n\n".join(chunks) or "[PDF contains no extractable text]"


def _read_attachment_content(
    path: str,
    upload_id: str,
    display_name: str,
    mime: str,
    upload_handler: Any,
    owner: Optional[str],
) -> str:
    extension = os.path.splitext(display_name)[1].lower()

    if mime.startswith("image/"):
        vision_path = os.path.join(upload_handler.upload_dir, ".vision", upload_id + ".txt")
        if (
            upload_handler._inside_upload_dir(vision_path)
            and os.path.isfile(vision_path)
        ):
            return _read_text(vision_path)
        # Historical images may not have a cached caption (for example when a
        # vision-capable main model saw the bytes directly).  Reuse the same
        # owner-aware vision processor used by current-turn attachments.
        from src.document_processor import analyze_image_with_vl_result

        result = analyze_image_with_vl_result(path, owner=owner)
        return str((result or {}).get("text") or "[Image has no available description]")

    if mime == "application/pdf" or extension == ".pdf":
        return _read_pdf(path)

    if extension in {".docx", ".pptx", ".xlsx", ".xls", ".epub"}:
        from src.markitdown_runtime import convert_to_markdown

        return convert_to_markdown(path) or "[Document contains no extractable text]"

    text_extensions = {
        ".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts",
        ".jsx", ".tsx", ".html", ".htm", ".css", ".xml", ".yml",
        ".yaml", ".nix", ".sql", ".sh", ".bash", ".c", ".cpp", ".h",
        ".java", ".go", ".rs", ".php", ".rb",
    }
    if mime.startswith("text/") or extension in text_extensions:
        return _read_text(path)

    return (
        f"[No safe text extractor is available for this {mime or 'binary'} "
        "attachment]"
    )


class ReadAttachmentTool:
    """Read one upload without granting generic filesystem access."""

    async def execute(self, content: str, ctx: dict) -> dict:
        upload_id = parse_attachment_id(content)
        if not upload_id:
            return {
                "error": (
                    "read_attachment: pass an exact upload id or "
                    "odysseus://attachment/<id> URI; filesystem paths and "
                    "URL query/fragment suffixes are not accepted."
                ),
                "exit_code": 1,
            }

        upload_handler = ctx.get("upload_handler")
        if (
            upload_handler is None
            or not hasattr(upload_handler, "resolve_upload")
            or not hasattr(upload_handler, "_inside_upload_dir")
        ):
            return {
                "error": "read_attachment: attachment storage is unavailable for this request.",
                "exit_code": 1,
            }

        owner = ctx.get("owner")
        if not owner:
            try:
                from src.auth_helpers import _auth_disabled

                single_user_mode = bool(_auth_disabled())
            except Exception:
                single_user_mode = False
            if not single_user_mode:
                return {
                    "error": "read_attachment: attachment not found or not authorized.",
                    "exit_code": 1,
                }
        try:
            info = await asyncio.to_thread(
                upload_handler.resolve_upload,
                upload_id,
                owner=owner,
                allow_admin=False,
            )
        except Exception:
            logger.warning("Attachment resolution failed for %s", upload_id, exc_info=True)
            info = None

        # Deliberately use one response for absent and unauthorized identifiers
        # so the tool is not an attachment-existence oracle across owners.
        if not isinstance(info, dict):
            return {
                "error": "read_attachment: attachment not found or not authorized.",
                "exit_code": 1,
            }

        path = info.get("path")
        if (
            not isinstance(path, str)
            or not upload_handler._inside_upload_dir(path)
            or os.path.basename(path) != upload_id
            or not os.path.isfile(path)
        ):
            return {
                "error": "read_attachment: attachment not found or not authorized.",
                "exit_code": 1,
            }

        display_name = _safe_display_name(info, upload_id)
        mime = str(
            info.get("mime")
            or mimetypes.guess_type(display_name)[0]
            or "application/octet-stream"
        )
        if len(mime) > 255 or not re.fullmatch(
            r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+",
            mime,
        ):
            mime = "application/octet-stream"
        try:
            output = await asyncio.to_thread(
                _read_attachment_content,
                path,
                upload_id,
                display_name,
                mime,
                upload_handler,
                owner,
            )
        except Exception:
            logger.warning("Attachment extraction failed for %s", upload_id, exc_info=True)
            return {
                "error": "read_attachment: attachment content could not be extracted.",
                "exit_code": 1,
            }

        size = info.get("size")
        return {
            "output": _cap_output(output),
            "attachment": {
                "id": upload_id,
                "uri": _ATTACHMENT_URI_PREFIX + upload_id,
                "name": display_name,
                "mime": mime,
                "size": size if isinstance(size, (int, float)) else None,
            },
            "exit_code": 0,
        }
