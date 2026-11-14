"""Render user-supplied text to PDF without executing model-supplied code."""
from __future__ import annotations

import html
import io
import os
import re
import secrets


MAX_TEXT_BYTES = 1_000_000
MAX_PAGES = 100


def pdf_creation_requested(text: str) -> bool:
    """Recognize a direct request to create or export a PDF, not to read one."""
    value = str(text or "")
    verbs = r"(?:create|make|generate|save|export|produce|convert|write|build|render)"
    if re.search(r"\b(?:do not|don't|never|avoid|without)\s+(?:ever\s+)?" + verbs + r"\b", value, re.I):
        return False
    direct = (r"\b(?:create|make|generate|produce|build|render|write|save)\s+"
              r"(?:(?:me|us|a|an|the|new|sample|example|simple|one[- ]page)\s+){0,6}pdf\b")
    conversion = r"\b" + verbs + r"\b[^.!?\n]{0,180}\b(?:as|to|into)\s+(?:a\s+)?pdf\b"
    named_pdf = (r"\b" + verbs + r"\s+(?:(?:a|the|new|file|named|called)\s+){0,4}"
                 r"[\w.-]+\.pdf\b")
    return bool(re.search(direct + "|" + conversion + "|" + named_pdf, value, re.I))


def single_pdf_request(text: str) -> bool:
    """Identify a simple PDF request that needs no additional operation."""
    if not pdf_creation_requested(text):
        return False
    # Filenames and quoted document content are data, not follow-up actions.
    text = re.sub(r'"[^"\n]*"|\b[\w.-]+\.[A-Za-z0-9]{1,8}\b', '', str(text or ''))
    return not re.search(
        r"\b(?:then|also|afterwards?|subsequently)\b|"
        r"\b(?:send|email|upload|share|search|browse|research|download|"
        r"summari[sz]e|translate|edit|update|delete|schedule|verify|check|inspect|test|review)\b|"
        r"\b(?:and|plus)\s+(?:a\s+|an\s+|the\s+|another\s+)?"
        r"(?:file|document|spreadsheet|image|presentation|csv|docx|xlsx|pdf)\b",
        text, re.I,
    )


def render_pdf(text: str) -> bytes:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("PDF content must contain text")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("PDF content exceeds the 1 MB limit")
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("PDF creation requires the optional PyMuPDF package") from exc

    # Escape all input: no HTML, external images, links or styles are loaded.
    # Basic Markdown headings remain readable; other lines are literal text.
    chunks = []
    for line in text.splitlines():
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            chunks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        elif line.strip():
            chunks.append(f"<p>{html.escape(line)}</p>")
        else:
            chunks.append("<p>&#160;</p>")
    document = "<html><body>" + "\n".join(chunks) + "</body></html>"
    font_path = next((p for p in (
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ) if os.path.isfile(p)), None)
    font_css = ""
    archive = None
    if font_path:
        archive = pymupdf.Archive(os.path.dirname(font_path))
        font_css = ('@font-face { font-family: BodyFont; src: url("' +
                    os.path.basename(font_path) + '"); } ')
    story = pymupdf.Story(html=document, archive=archive, user_css=(
        font_css + "body { font-family: BodyFont, sans-serif; font-size: 11pt; line-height: 1.35; } "
        "p { margin: 0 0 5pt 0; } h1 { font-size: 22pt; } "
        "h2 { font-size: 17pt; } h3 { font-size: 13pt; }"
    ))
    output = io.BytesIO()
    writer = pymupdf.DocumentWriter(output)
    page_rect = pymupdf.paper_rect("a4")
    content_rect = page_rect + (48, 48, -48, -48)
    try:
        for _ in range(MAX_PAGES):
            more, filled = story.place(content_rect)
            if pymupdf.Rect(filled).is_empty:
                raise ValueError("PDF renderer could not fit the document on a page")
            device = writer.begin_page(page_rect)
            story.draw(device)
            writer.end_page()
            if not more:
                break
        else:
            raise ValueError(f"PDF content exceeds the {MAX_PAGES}-page limit")
    finally:
        writer.close()
    result = output.getvalue()
    with pymupdf.open(stream=result, filetype="pdf") as check:
        if not check.page_count or not any(page.get_text().strip() for page in check):
            raise ValueError("PDF verification found no readable text")
        # The system Unicode font is very large. Embed only glyphs used by
        # this document so even a one-page PDF stays reasonably sized.
        check.subset_fonts()
        result = check.tobytes(garbage=4, deflate=True)
    return result


def publish_new_pdf(path: str, data: bytes, validate=None) -> None:
    """Publish atomically and never replace an existing artifact."""
    directory = os.path.dirname(path) or "."
    if not os.path.isdir(directory):
        raise ValueError("PDF parent directory does not exist")
    temporary_path = os.path.join(directory, f".odysseus-pdf-{secrets.token_hex(16)}.tmp")
    fd = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            fd = None
            output.write(data)
        if validate is not None:
            validate()
        os.link(temporary_path, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def register_pdf_download(data: bytes, filename: str, owner: str | None) -> str:
    """Register the generated PDF with the existing owner-checked upload store."""
    from fastapi import UploadFile
    from src.tool_utils import get_upload_handler
    from src.upload_handler import is_valid_upload_id
    handler = get_upload_handler()
    if handler is None:
        raise RuntimeError('chat download storage is unavailable')
    upload = UploadFile(file=io.BytesIO(data), filename=os.path.basename(filename))
    info = handler.save_upload(upload, client_ip='agent-pdf:' + str(owner or 'local'), owner=owner)
    upload_id = str(info.get('id') or '')
    if not is_valid_upload_id(upload_id):
        raise RuntimeError('chat download storage returned an invalid file ID')
    # A failed index write must not produce a link that looks usable but is not.
    if not any(row.get('id') == upload_id and row.get('owner') == owner
               for row in handler._load_upload_index().values() if isinstance(row, dict)):
        raise RuntimeError('chat download could not be registered')
    return '/api/upload/' + upload_id
