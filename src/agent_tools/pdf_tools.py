"""create_pdf: turn chat content into a downloadable PDF for the requesting user.

The model only chooses the title and the Markdown; ``src.pdf_export`` owns the
layout. Small local models often call the tool with no content after "make a
pdf of that", or with the injected date/time context as the content, so both
fall back to the previous assistant answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

from src.ai_interaction import get_session_manager
from src.pdf_export import PdfExportError, render_markdown_pdf
from src.tool_utils import get_upload_handler
from src.upload_handler import secure_filename

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    return ""


# Lines of the per-turn "## Current date and time" context (src/user_time.py),
# verbatim or reworded by the model ("**User local time:** 9:02 PM").
_DATE_CONTEXT_LINE = re.compile(
    r"current\s+date\s+and\s+time|^\W*(?:today|tomorrow)\s+is\s+\w+day\b|local\s+time\b|"
    r"\butc\s+time\b|use\s+this\s+for\s+any\s+'today'",
    re.IGNORECASE,
)
_FENCE_LINE = re.compile(r"^\s*```\w*\s*$")
# Replies the app itself writes in place of an answer.
_APPROVAL_PROMPT = "Allow this task to continue?"
_REFUSAL_PREFIX = "I can’t perform this request"


def _strip_date_context(markdown: str) -> str:
    kept = [line for line in markdown.splitlines() if not _DATE_CONTEXT_LINE.search(line)]
    text = "\n".join(kept)
    # Fences left empty by the removal ("```plaintext" + "```") go too.
    text = re.sub(r"^\s*```\w*\s*\n(?:\s*\n)*\s*```\s*$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _has_substance(markdown: str) -> bool:
    lines = [line for line in markdown.splitlines() if not _FENCE_LINE.match(line)]
    return len(re.sub(r"[\W_]+", "", "".join(lines))) >= 3


def _is_answer(text: str) -> bool:
    return bool(
        text
        and not text.startswith(_REFUSAL_PREFIX)
        and not text.rstrip().endswith("?")
        and _has_substance(_strip_date_context(text))
    )


def _previous_assistant_answer(session_id: Optional[str]) -> str:
    """The assistant reply to the message before the current PDF request, or "".

    Approval prompts saved during this turn and the request itself are
    skipped. Only that one reply counts: if it was a refusal or a question,
    there is nothing to export, and an older answer would be a guess.
    """
    if not session_id:
        return ""
    try:
        history = list(get_session_manager().get_session(session_id).history or ())
    except Exception:
        return ""

    def text_of(message) -> str:
        return _message_text(getattr(message, "content", "")).strip()

    while history and getattr(history[-1], "role", None) == "assistant" and text_of(history[-1]) == _APPROVAL_PROMPT:
        history.pop()
    if history and getattr(history[-1], "role", None) == "user":
        history.pop()
    for message in reversed(history):
        if getattr(message, "role", None) == "user":
            return ""
        if getattr(message, "role", None) == "assistant" and text_of(message):
            text = text_of(message)
            return _strip_date_context(text) if _is_answer(text) else ""
    return ""


def title_from_markdown(markdown: str) -> str:
    """The first heading, else the first sentence, capped at 80 characters."""
    for line in str(markdown or "").splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            return heading.group(1).strip("*_ ")[:80]
    first = re.split(r"(?<=[.!?])\s", str(markdown or "").strip(), maxsplit=1)[0]
    return first.rstrip(".!?").strip("*_ ")[:80] or "Document"


def _parse_args(content: str) -> dict:
    raw = (content or "").strip()
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {"content_markdown": raw}


def _pdf_filename(requested: Any, title: str) -> str:
    stem = os.path.basename(str(requested or "").strip()) or title or "document"
    if stem.lower().endswith(".pdf"):
        stem = stem[:-4]
    return f"{secure_filename(stem)[:80] or 'document'}.pdf"


class CreatePdfTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        args = _parse_args(content)
        title = " ".join(str(args.get("title") or "").split())
        markdown = str(args.get("content_markdown") or args.get("content") or "").strip()
        session_id = ctx.get("session_id") if isinstance(ctx, dict) else None
        owner = ctx.get("owner") if isinstance(ctx, dict) else None
        requested_filename = args.get("filename")
        markdown = _strip_date_context(markdown)
        if markdown and not _has_substance(markdown):
            markdown = ""
        if not markdown:
            markdown = _previous_assistant_answer(session_id)
            if markdown and _DATE_CONTEXT_LINE.search(title):
                # "Current Date and Time Context" named the echoed content, not the answer.
                title, requested_filename = title_from_markdown(markdown), None
        if not markdown:
            return {
                "error": (
                    "create_pdf: there is no answer in this chat to put in the PDF yet. "
                    "Write the content first and pass it as content_markdown, or ask the "
                    "user what the PDF should contain."
                ),
                "exit_code": 1,
            }

        handler = get_upload_handler()
        if handler is None:
            return {"error": "create_pdf: file storage is not available.", "exit_code": 1}

        filename = _pdf_filename(requested_filename, title)
        try:
            data = await asyncio.to_thread(render_markdown_pdf, title, markdown)
            saved = await asyncio.to_thread(handler.save_generated_file, data, filename, PDF_MIME, owner)
        except (PdfExportError, ValueError) as exc:
            return {"error": f"create_pdf: {exc}", "exit_code": 1}

        attachment = {
            "id": saved["id"],
            "name": saved["name"],
            "mime": saved["mime"],
            "size": saved["size"],
        }
        logger.info("create_pdf stored %s (%d bytes) for session %s", saved["name"], saved["size"], session_id)
        return {
            "output": (
                f"Created the PDF {saved['name']} ({saved['size'] // 1024 or 1} KB). "
                "It is attached to this reply as a download; tell the user it is ready."
            ),
            "exit_code": 0,
            "attachment": attachment,
        }
