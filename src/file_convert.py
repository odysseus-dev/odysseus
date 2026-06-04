"""File-format conversion engine for the Settings → Convert tab.

A small, dependency-light converter in the spirit of convert.io. Two families:

* **Images** — round-tripped through Pillow (png/jpg/webp/gif/bmp/tiff → each
  other, plus → pdf). Pillow ships transitively via ``qrcode[pil]`` so this
  family works out of the box.
* **Documents** — extracted to Markdown/plain-text via the optional
  ``markitdown`` dependency (docx/pptx/xlsx/xls/epub/csv/html), reusing the same
  runtime the chat-attachment and RAG paths use (:mod:`src.markitdown_runtime`).
  PDFs are extracted with ``pypdf`` to keep the MIT core pure.

Conversions run fully in-memory — nothing is written to disk or persisted. The
public surface is :func:`supported_targets` (drives the UI dropdown) and
:func:`convert_file` (does the work).
"""

from __future__ import annotations

import io
import logging
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)


class ConversionError(Exception):
    """Raised when a conversion is unsupported or fails. Message is user-facing."""


# ── Image family ───────────────────────────────────────────────────────────
# Inputs we can open and the targets we can write. Targets exclude PDF-only
# inputs; PDF is write-only here (an image *to* a one-page PDF).
IMAGE_INPUT_EXTS = frozenset({
    "png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif", "ico",
})
IMAGE_OUTPUT_EXTS = ("png", "jpg", "webp", "gif", "bmp", "tiff", "pdf", "ico")

# Pillow's save format name keyed by our lowercase extension.
_PIL_SAVE_FORMAT = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
    "tif": "TIFF",
    "ico": "ICO",
    "pdf": "PDF",
}

# Targets that cannot hold an alpha channel — flatten onto white first.
_NO_ALPHA = {"jpg", "jpeg", "bmp", "pdf"}

# ── Document family ────────────────────────────────────────────────────────
# Extracted to Markdown via markitdown (see src.markitdown_runtime), plus PDF
# via pypdf. Output is always Markdown or plain text.
DOC_MARKITDOWN_EXTS = frozenset({
    "docx", "pptx", "xlsx", "xls", "epub", "csv", "html", "htm",
})
DOC_INPUT_EXTS = DOC_MARKITDOWN_EXTS | frozenset({"pdf"})
DOC_OUTPUT_EXTS = ("md", "txt")

_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "ico": "image/x-icon",
    "pdf": "application/pdf",
    "md": "text/markdown",
    "txt": "text/plain",
}


def _ext(name: str) -> str:
    """Lowercase extension without the dot (jpeg/tif normalised by callers)."""
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def supported_targets(src_name: str) -> List[str]:
    """Return the list of target extensions a given source file can convert to.

    Empty list means the source type is unsupported. Used to populate the UI's
    target dropdown after a file is chosen.
    """
    ext = _ext(src_name)
    if ext in IMAGE_INPUT_EXTS:
        # Normalise jpeg→jpg / tif→tiff so the source never appears as its own
        # target.
        norm = {"jpeg": "jpg", "tif": "tiff"}.get(ext, ext)
        return [t for t in IMAGE_OUTPUT_EXTS if t != norm]
    if ext in DOC_INPUT_EXTS:
        return list(DOC_OUTPUT_EXTS)
    return []


def media_type_for(ext: str) -> str:
    return _MEDIA_TYPES.get(ext.lower().lstrip("."), "application/octet-stream")


def convert_file(data: bytes, src_name: str, target: str) -> Tuple[bytes, str, str]:
    """Convert ``data`` (named ``src_name``) to ``target`` extension.

    Returns ``(output_bytes, output_filename, media_type)``. Raises
    :class:`ConversionError` with a user-facing message on any failure.
    """
    src_ext = _ext(src_name)
    target = (target or "").lower().lstrip(".")
    if not target:
        raise ConversionError("No target format selected.")

    allowed = supported_targets(src_name)
    if not allowed:
        raise ConversionError(
            f"Unsupported source file type '.{src_ext or '?'}'."
        )
    if target not in allowed:
        raise ConversionError(
            f"Can't convert .{src_ext} to .{target}. "
            f"Supported: {', '.join(allowed)}."
        )

    base = os.path.splitext(os.path.basename(src_name or "file"))[0] or "file"
    out_name = f"{base}.{target}"

    if src_ext in IMAGE_INPUT_EXTS:
        out = _convert_image(data, target)
    else:
        out = _convert_document(data, src_name, src_ext, target)

    return out, out_name, media_type_for(target)


def _convert_image(data: bytes, target: str) -> bytes:
    try:
        from PIL import Image  # transitive via qrcode[pil]
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ConversionError(
            "Image conversion requires Pillow. Install it with `pip install Pillow`."
        ) from exc

    fmt = _PIL_SAVE_FORMAT.get(target)
    if not fmt:
        raise ConversionError(f"Unknown image format '{target}'.")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ConversionError("Could not read the image — file may be corrupt.") from exc

    # Animated GIFs collapse to their first frame for static targets; that's the
    # sensible default and avoids surprising multi-frame output.
    if getattr(img, "is_animated", False) and target != "gif":
        img.seek(0)

    if target in _NO_ALPHA:
        # Flatten transparency onto white so JPEG/BMP/PDF don't render black.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
    elif target == "gif":
        # GIF is paletted; convert anything else down.
        if img.mode not in ("P", "L"):
            img = img.convert("P", palette=Image.ADAPTIVE)
    elif img.mode == "P":
        img = img.convert("RGBA")

    buf = io.BytesIO()
    save_kwargs = {}
    if target in ("jpg", "jpeg"):
        save_kwargs.update(quality=92)
    if target == "ico":
        # ICO has a 256px ceiling per dimension.
        img.thumbnail((256, 256))
    try:
        img.save(buf, format=fmt, **save_kwargs)
    except Exception as exc:
        raise ConversionError(f"Failed to write {target.upper()}: {exc}") from exc
    return buf.getvalue()


def _convert_document(data: bytes, src_name: str, src_ext: str, target: str) -> bytes:
    if src_ext == "pdf":
        markdown = _pdf_to_text(data)
    else:
        markdown = _markitdown_bytes(data, src_name)

    if markdown is None:
        raise ConversionError(
            "Document extraction is unavailable. Install optional dependencies "
            "with `pip install -r requirements-optional.txt`."
        )

    # .txt strips most Markdown decoration; .md keeps it verbatim.
    text = markdown if target == "md" else _strip_markdown(markdown)
    return text.encode("utf-8")


def _markitdown_bytes(data: bytes, src_name: str) -> str | None:
    """Run markitdown over in-memory bytes via a temp file (its API is path-based)."""
    import tempfile

    from src.markitdown_runtime import convert_to_markdown

    suffix = os.path.splitext(src_name)[1] or ""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return convert_to_markdown(tmp_path)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _pdf_to_text(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader  # core dependency
    except ImportError:  # pragma: no cover - core dep, should be present
        return None
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for i, page in enumerate(reader.pages, 1):
            txt = (page.extract_text() or "").strip()
            if txt:
                parts.append(f"## Page {i}\n\n{txt}")
        return "\n\n".join(parts) if parts else "*(No extractable text in PDF.)*"
    except Exception as exc:
        raise ConversionError(f"Failed to read PDF: {exc}") from exc


def _strip_markdown(md: str) -> str:
    """Best-effort Markdown → plain text for the .txt target."""
    import re

    text = md
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)   # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                  # bold
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"\1", text)         # italic
    text = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", text, flags=re.S)  # code
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)        # links
    return text
