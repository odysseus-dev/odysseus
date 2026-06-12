"""Bridge between extracted PDF form fields and the document editor.

Design: the user edits the form as readable markdown — labels as bullets,
values as plain text — exactly like any other document in the editor.

A hidden HTML-comment front-matter pointer at the top of the markdown
links the document back to the source PDF and the field-schema sidecar:

    <!-- pdf_form_source upload_id="abc.pdf" fields="441" -->

The export route reads that pointer to find the source PDF + sidecar JSON,
then asks an LLM to map markdown values back to AcroForm field names.
"""

import json
import logging
import os
import re
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)


_FRONT_MATTER_RE = re.compile(
    r'<!--\s*pdf_form_source\s+upload_id="(?P<upload_id>[^"]+)"(?:\s+fields="(?P<fields>\d+)")?\s*-->'
)

# Freeform annotation bullet — mirrors the JS regex in static/js/document.js.
# Coords are page percentages (0–100); kind/lh are optional for backward compat.
_ANNOTATION_RE = re.compile(
    r'^[ \t]*-\s+(?P<value>.*?)\s*<!--\s*annotation\s+id=(?P<id>[\w-]+)\s+page=(?P<page>\d+)\s+x=(?P<x>[\d.]+)\s+y=(?P<y>[\d.]+)\s+w=(?P<w>[\d.]+)\s+h=(?P<h>[\d.]+)(?:\s+kind=(?P<kind>\w+))?(?:\s+lh=(?P<lh>[\d.]+))?\s*-->[ \t]*$',
    re.MULTILINE,
)


def _unescape_annotation_value(s: str) -> str:
    """Inverse of the JS _escapeAnnotationValue: \\\\n → newline, \\\\\\\\ → \\."""
    out: list[str] = []
    i = 0
    n = len(s or "")
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_markdown_annotations(content: str) -> list[dict]:
    """Return the list of freeform annotation dicts embedded in a doc's markdown.

    Each entry: {id, page, x, y, w, h, kind, line_height, value}.
    Coordinates are page percentages (0–100) — caller scales them to PDF user
    units when stamping.
    """
    out: list[dict] = []
    for m in _ANNOTATION_RE.finditer(content or ""):
        # One malformed bullet (e.g. user hand-edited markdown leaving
        # `x=12.3.4`) must NOT drop every other annotation in the doc.
        # Skip the bad line, keep going.
        try:
            raw = m.group("value")
            value = "" if raw == "_(empty)_" else _unescape_annotation_value(raw)
            out.append({
                "id": m.group("id"),
                "page": int(m.group("page")),
                "x": float(m.group("x")),
                "y": float(m.group("y")),
                "w": float(m.group("w")),
                "h": float(m.group("h")),
                "kind": m.group("kind") or "text",
                "line_height": float(m.group("lh")) if m.group("lh") else 1.3,
                "value": value,
            })
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping malformed annotation bullet near offset {m.start()}: {e}")
            continue
    return out

# Plain-PDF marker: same shape as the form-source marker but emitted for any
# imported PDF (no AcroForm fields). Lets the existing render-pages /
# render-pdf / page-png endpoints serve a viewer for non-form PDFs too.
_PLAIN_FRONT_MATTER_RE = re.compile(
    r'<!--\s*pdf_source\s+upload_id="(?P<upload_id>[^"]+)"\s*-->'
)

# Bullet line emitted by render_form_as_markdown. The trailing comment is the
# anchor we rely on to recover the field name even after the user/model edits
# the value. The field name is percent-encoded so spaces, newlines, parens
# and other special chars in raw AcroForm names don't break parsing.
#   - **label:** value <!-- field=NAME-ENC type=text -->
#   - **label** [opts]: value <!-- field=NAME-ENC type=choice -->
#   - [x] **label** <!-- field=NAME-ENC type=checkbox -->
_FIELD_BULLET_RE = re.compile(
    r'^\s*-\s+(?P<body>.*?)\s*<!--\s*field=(?P<name>[A-Za-z0-9_.%-]+)\s+type=(?P<type>\w+)\s*-->\s*$'
)


def _encode_name(name: str) -> str:
    """Percent-encode any char that's not a regex/HTML-comment-safe token.

    Keeps A-Z a-z 0-9 _ . - . Everything else (spaces, newlines, parens,
    commas, quotes, etc.) becomes %XX. JS side must use the same scheme.
    """
    out = []
    for ch in name or "":
        if ch.isalnum() or ch in ("_", ".", "-"):
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append(f"%{b:02X}")
    return "".join(out)


def _decode_name(enc: str) -> str:
    """Inverse of _encode_name."""
    import urllib.parse
    return urllib.parse.unquote(enc or "")
# Label segment is non-greedy (.+?) so labels containing '*' — the near-universal
# required-field marker, e.g. "Email *" — are tolerated, while still splitting at
# the FIRST ':**' / '**[' so a value that itself contains ':**' is preserved.
# (The old [^*]+ refused to match any label with an asterisk and silently
# dropped that field's value on export.)
_TEXT_VALUE_RE = re.compile(r'\*\*.+?:\*\*\s*(?P<value>.*)$')
_CHOICE_VALUE_RE = re.compile(r'\*\*.+?\*\*\s*\[[^\]]*\]\s*:\s*(?P<value>.*)$')
_CHECKBOX_VALUE_RE = re.compile(r'^\s*\[(?P<state>[xX ])\]')

_PLACEHOLDERS = {"_(empty)_", "_(not selected)_", "_(empty)_.", "_(unsigned)_"}


def sidecar_path(pdf_path: str) -> str:
    """Path of the field-schema JSON stored next to a PDF upload."""
    return pdf_path + ".fields.json"


def save_field_sidecar(pdf_path: str, fields: list[dict[str, Any]]) -> str:
    """Persist the field schema next to its source PDF. Returns the sidecar path."""
    path = sidecar_path(pdf_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fields, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write field sidecar {path}: {e}")
    return path


def load_field_sidecar(pdf_path: str) -> Optional[list[dict[str, Any]]]:
    """Return field schema for a PDF, or None if no sidecar exists."""
    path = sidecar_path(pdf_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read field sidecar {path}: {e}")
        return None


def find_source_upload_id(content: str) -> Optional[str]:
    """Return the upload_id from the doc's front-matter pointer, or None.

    Matches both the form-source marker (`pdf_form_source`) used for fillable
    PDFs and the plain marker (`pdf_source`) used for any imported PDF.
    Rejects malformed ids (path traversal, wrong shape) before any lookup.
    """
    from src.upload_handler import is_valid_upload_id

    m = _FRONT_MATTER_RE.search(content or "") or _PLAIN_FRONT_MATTER_RE.search(content or "")
    if not m:
        return None
    upload_id = m.group("upload_id")
    if not is_valid_upload_id(upload_id):
        logger.warning("Ignoring invalid pdf_source upload_id in document content: %r", upload_id)
        return None
    return upload_id


def render_plain_pdf_markdown(upload_id: str, title: str, body_text: Optional[str] = None) -> str:
    """Build the markdown wrapper for a non-form PDF imported into the editor.

    The hidden front-matter pointer links the doc to the source PDF so the
    viewer endpoints (render-pages / page-png) can serve the rendered pages.
    Any extracted text is included below the title so the markdown source view
    is still useful (search, copy/paste, AI tools).
    """
    lines: list[str] = [
        f'<!-- pdf_source upload_id="{upload_id}" -->',
        "",
        f"# {title}",
        "",
    ]
    if body_text and body_text.strip():
        lines.append(body_text.strip())
        lines.append("")
    return "\n".join(lines) + "\n"


def create_plain_pdf_document(
    session_id: str,
    upload_id: str,
    title: str,
    body_text: Optional[str] = None,
) -> Optional[str]:
    """Create a markdown Document for a non-form PDF and set it active.

    Returns the new doc_id, or None on failure. Pairs with `find_source_upload_id`
    so the existing /render-pages and /page/{n}.png endpoints can serve the
    pages without form-field overlays.
    """
    from src.database import SessionLocal, Document, DocumentVersion, Session as DbSession
    from src.agent_tools.document_tools import set_active_document

    content = render_plain_pdf_markdown(upload_id, title, body_text)
    db = SessionLocal()
    try:
        doc_id = str(uuid.uuid4())
        ver_id = str(uuid.uuid4())
        _sess = db.query(DbSession).filter(DbSession.id == session_id).first()
        doc = Document(
            id=doc_id,
            session_id=session_id,
            title=title,
            language="markdown",
            current_content=content,
            version_count=1,
            is_active=True,
            owner=_sess.owner if _sess else None,
        )
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=content,
            summary="Imported from PDF",
            source="upload",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        set_active_document(doc_id)
        return doc_id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create plain PDF document: {e}")
        return None
    finally:
        db.close()


def parse_markdown_to_values(content: str) -> dict[str, Any]:
    """Recover {field_name: value} from the rendered markdown.

    Deterministic — relies on the hidden HTML-comment field markers in each
    bullet. Lines whose markers are intact survive arbitrary edits to label
    and value text. Lines whose markers were stripped are silently skipped;
    those fields just won't be filled in the output PDF.

    Empty placeholders ("_(empty)_", "_(not selected)_") map to "".
    Checkbox state comes from the leading `[ ]` / `[x]` marker.
    """
    values: dict[str, Any] = {}
    for line in (content or "").splitlines():
        m = _FIELD_BULLET_RE.match(line)
        if not m:
            continue
        name = _decode_name(m.group("name"))
        ftype = m.group("type")
        body = m.group("body")

        if ftype == "checkbox":
            cb = _CHECKBOX_VALUE_RE.match(body)
            values[name] = bool(cb and cb.group("state").lower() == "x")
            continue

        raw = ""
        if ftype == "choice":
            cm = _CHOICE_VALUE_RE.search(body)
            if cm:
                raw = cm.group("value").strip()
        else:
            tm = _TEXT_VALUE_RE.search(body)
            if tm:
                raw = tm.group("value").strip()

        if raw in _PLACEHOLDERS:
            raw = ""
        values[name] = raw
    return values


def _checkbox_marker(value: Any) -> str:
    return "[x]" if value else "[ ]"


def _flatten(value: Any) -> str:
    """Collapse PDF newline runs (\\r, \\n) so a value fits on one bullet line."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _format_field_bullet(f: dict[str, Any]) -> str:
    """Render one form field as a markdown bullet line.

    Hidden HTML comment carries the percent-encoded field name so the
    export/save logic has a robust anchor regardless of what whitespace,
    parens, or special chars appear in the raw AcroForm field name. The
    visible label is the human-readable bit.

    Signature fields encode the chosen signature ID inline as
    `signature:<id>` so the picker selection persists in the doc and the
    export route can stamp the saved PNG without extra state.
    """
    label = _flatten(f.get("label")) or f["name"]
    name = _encode_name(f["name"])
    ftype = f["type"]
    value = _flatten(f.get("value"))

    if ftype == "checkbox":
        body = f'{_checkbox_marker(value)} **{label}**'
    elif ftype == "choice":
        opts = f.get("options") or []
        opts_str = " / ".join(opts) if opts else ""
        shown = value if value else "_(not selected)_"
        body = f'**{label}** [{opts_str}]: {shown}'
    elif ftype == "signature":
        shown = value if (value and value.startswith("signature:")) else "_(unsigned)_"
        body = f'**{label}:** {shown}'
    else:
        shown = value if value else "_(empty)_"
        body = f'**{label}:** {shown}'

    return f'- {body} <!-- field={name} type={ftype} -->'


def render_form_as_markdown(
    fields: list[dict[str, Any]],
    upload_id: str,
    title: str,
    intro_text: Optional[str] = None,
) -> str:
    """Build the markdown document the user edits in the editor.

    Layout:
      front-matter pointer (hidden in editor render but present in source)
      title
      one-paragraph intro + how to export
      one section per page, bulleted fields
    """
    lines: list[str] = [
        f'<!-- pdf_form_source upload_id="{upload_id}" fields="{len(fields)}" -->',
        "",
        f"# {title}",
        "",
        "Edit values in place — change the text after each label, tick/untick "
        "checkboxes, and pick one of the listed options for choice fields. "
        "When done, click **Export PDF** to download the filled form.",
        "",
    ]
    last_page: Optional[int] = None
    for f in fields:
        if f["page"] != last_page:
            lines.append("")
            lines.append(f"## Page {f['page']}")
            lines.append("")
            last_page = f["page"]
        lines.append(_format_field_bullet(f))

    if intro_text:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Original form text")
        lines.append("")
        lines.append(intro_text.strip())

    return "\n".join(lines) + "\n"


def create_form_markdown_document(
    session_id: str,
    fields: list[dict[str, Any]],
    upload_id: str,
    title: str,
    intro_text: Optional[str] = None,
) -> Optional[str]:
    """Create a markdown Document for an editable form and set it active.

    Returns the new doc_id, or None on failure. The Document's language is
    "markdown" — the form-ness is signalled only by the front-matter pointer
    inside the content, which the export route looks for.
    """
    from src.database import SessionLocal, Document, DocumentVersion, Session as DbSession
    from src.agent_tools.document_tools import set_active_document

    content = render_form_as_markdown(fields, upload_id, title, intro_text=intro_text)
    db = SessionLocal()
    try:
        doc_id = str(uuid.uuid4())
        ver_id = str(uuid.uuid4())
        _sess = db.query(DbSession).filter(DbSession.id == session_id).first()
        doc = Document(
            id=doc_id,
            session_id=session_id,
            title=title,
            language="markdown",
            current_content=content,
            version_count=1,
            is_active=True,
            owner=_sess.owner if _sess else None,
        )
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=content,
            summary="Imported from PDF form",
            source="upload",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        set_active_document(doc_id)
        return doc_id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create form markdown document: {e}")
        return None
    finally:
        db.close()
