from typing import Any, Dict, List, Optional
import hashlib
import html
import difflib
import logging
import re
from src.constants import MAX_READ_CHARS
from src.tool_approvals import document_content_digest
from src.tool_utils import _parse_tool_args, get_upload_handler
from src.upload_handler import reserve_upload_references

logger = logging.getLogger(__name__)


_DOCUMENT_SEARCH_STOPWORDS = frozenset({
    'a', 'an', 'and', 'any', 'about', 'document', 'documents', 'for', 'in',
    'my', 'of', 'on', 'or', 'plans', 'the', 'to',
})


def _document_search_tokens(value: str) -> list[str]:
    return [
        token for token in re.findall(r'[a-z0-9]+', str(value or '').lower())
        if token not in _DOCUMENT_SEARCH_STOPWORDS
    ]


def _rank_document_search(docs, search_text: str):
    """Prefer phrase/all-term matches, then broaden to any meaningful term."""
    query = str(search_text or '').strip().lower()
    terms = _document_search_tokens(query)
    scored = []
    for position, doc in enumerate(docs):
        haystack = ' '.join((
            str(getattr(doc, 'title', '') or ''),
            str(getattr(doc, 'current_content', '') or ''),
        )).lower()
        haystack_terms = set(_document_search_tokens(haystack))
        matched = sum(term in haystack_terms for term in terms)
        strict = bool(query and query in haystack) or bool(terms and matched == len(terms))
        scored.append((doc, strict, matched, position))
    strict_matches = [row for row in scored if row[1]]
    candidates = strict_matches or [row for row in scored if row[2] > 0]
    return [row[0] for row in sorted(candidates, key=lambda row: (-row[2], row[3]))]


def _missing_document_upload(owner: Optional[str], content: Any) -> Optional[str]:
    """Reserve explicit upload URLs before an agent persists document text."""
    return reserve_upload_references(get_upload_handler(), owner, content)

# ---------------------------------------------------------------------------
# Active document state
# ---------------------------------------------------------------------------

_active_document_id: Optional[str] = None
_active_model: Optional[str] = None


def set_active_document(doc_id: Optional[str]):
    """Set the active document ID for document tool execution."""
    global _active_document_id
    _active_document_id = doc_id


def set_active_model(model: Optional[str]):
    """Set the current model name for version summaries."""
    global _active_model
    _active_model = model


def get_active_document():
    return _active_document_id


def clear_active_document(doc_id: Optional[str] = None) -> bool:
    """Clear the in-memory active-document pointer.

    With ``doc_id`` given, only clears when it matches the current pointer, so a
    different active document is left untouched. Returns True if it was cleared.

    Called when a document is detached from its session or deleted (its tab is
    closed): without this, the stale pointer makes the last-resort doc-injection
    path re-surface a closed document in a later, unrelated chat — even one whose
    session no longer matches — because an unlinked doc has session_id NULL (#1160).
    """
    global _active_document_id
    if doc_id is None or _active_document_id == doc_id:
        _active_document_id = None
        return True
    return False


def _owned_document_query(query, Document, owner: Optional[str]):
    if owner is None:
        # A bare Python `False` is not a valid SQL expression — SQLAlchemy 1.4
        # deprecates it and 2.0 raises ArgumentError. Use the SQL `false()`
        # literal to return zero rows for an unscoped (owner-less) query.
        from sqlalchemy import false
        return query.filter(false())
    return query.filter(Document.owner == owner)


def _get_owned_document(db, Document, doc_id: str, owner: Optional[str], active_only: bool = False):
    q = db.query(Document).filter(Document.id == doc_id)
    if active_only:
        q = q.filter(Document.is_active == True)
    q = _owned_document_query(q, Document, owner)
    return q.first()


def _most_recent_owned_document(db, Document, owner: Optional[str], active_only: bool = False):
    q = db.query(Document)
    if active_only:
        q = q.filter(Document.is_active == True)
    q = _owned_document_query(q, Document, owner)
    return q.order_by(Document.updated_at.desc()).first()


def _approved_document_version_error(doc: Any, ctx: dict) -> Optional[Dict]:
    """Reject a sealed document action when its target changed meanwhile."""
    expected_version = ctx.get("expected_document_version")
    expected_digest = (
        str(ctx.get("expected_document_digest") or "").strip().lower()
    )
    if expected_version is None and not expected_digest:
        return None
    try:
        version_unchanged = (
            expected_version is None
            or int(getattr(doc, "version_count", -1)) == int(expected_version)
        )
    except (TypeError, ValueError):
        version_unchanged = False
    content_unchanged = True
    if expected_digest:
        content_unchanged = (
            doc is not None
            and document_content_digest(getattr(doc, "current_content", ""))
            == expected_digest
        )
    if version_unchanged and content_unchanged:
        return None
    return {
        "error": (
            "The target document changed after this action was proposed. "
            "Review the latest version and request the edit again."
        ),
        "exit_code": 1,
        "document_changed": True,
    }


# ---------------------------------------------------------------------------
# Document tools — create/update/edit/suggest living documents
# ---------------------------------------------------------------------------

def _sniff_doc_language(text: str) -> str:
    """Best-effort detect a document's language from its content when the model
    didn't specify one. Defaults to 'markdown' (prose). Recognizes the common
    markup/code types the editor supports so e.g. an SVG isn't saved as markdown."""
    import json as _json, re as _re2
    s = (text or "").strip()
    if not s:
        return "markdown"
    head = s[:600]
    hl = head.lower()
    if _looks_like_email_document(s):
        return "email"
    # Markup (unambiguous)
    if "<svg" in hl:
        return "svg"
    if hl.startswith("<?xml"):
        return "xml"
    if (hl.startswith("<!doctype html") or hl.startswith("<html")
            or _re2.search(r"<(div|body|head|p|span|table|button|h[1-6]|ul|ol|li|img)\b", hl)):
        return "html"
    # JSON
    if s[0] in "{[":
        try:
            _json.loads(s)
            return "json"
        except Exception:
            pass
    # Shebang
    first = s.split("\n", 1)[0].strip().lower()
    if first.startswith("#!"):
        return "python" if "python" in first else "bash"
    # Code by strong leading signals (line-anchored so prose with stray words won't match)
    if _re2.search(r"(?m)^\s*(def \w|class \w|import \w|from \w[\w.]* import )", s):
        return "python"
    if _re2.search(r"(?m)^\s*(function \w|const \w|let \w|export |import .* from )", s):
        return "javascript"
    if _re2.search(r"(?mi)^\s*(select .* from |create table |insert into |update \w)", s):
        return "sql"
    if _re2.search(r"(?m)^[.#]?[\w-]+\s*\{[^{}]*:[^{}]*;", s):
        return "css"
    return "markdown"

def _looks_like_email_document(text: str = "", title: str = "") -> bool:
    import re as _re
    title_l = (title or "").strip().lower()
    if title_l in {"new email", "new mail", "new message"}:
        return True
    s = (text or "").lstrip()
    if "\n---\n" in s and _re.search(r"(?im)^To:\s*", s) and _re.search(r"(?im)^Subject:\s*", s):
        return True
    return bool(_re.search(r"(?im)^To:\s*", s) and _re.search(r"(?im)^Subject:\s*", s))

def _split_email_header_body(text: str) -> tuple[str, str]:
    if "\n---\n" in (text or ""):
        header, body = (text or "").split("\n---\n", 1)
        return header.rstrip(), body.strip()
    return (text or "").strip(), ""

def _split_email_reply_history(body: str) -> tuple[str, str]:
    """Split draft body from quoted/original email history.

    Email reply docs keep the original thread below the user's new reply. Models
    often rewrite only the fresh reply body; this helper keeps the historical
    block from being wiped when update_document/edit_document replaces content.
    """
    text = body or ""
    literal = "---------- Previous message ----------"
    literal_idx = text.find(literal)
    if literal_idx >= 0:
        return text[:literal_idx].strip(), text[literal_idx:].strip()
    patterns = [
        r"(?m)^On .+ wrote:\s*$",
        r"(?m)^> .+",
    ]
    starts = []
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            starts.append(m.start())
    if not starts:
        return text.strip(), ""
    idx = min(starts)
    return text[:idx].strip(), text[idx:].strip()

def _merge_email_headers(old_header: str, new_header: str) -> str:
    """Preserve routing/threading metadata if a model omits it."""
    protected = (
        "In-Reply-To", "References", "X-Source-UID", "X-Source-Folder",
        "X-Attachments", "X-Forward-Attachments",
    )
    lines = [l for l in (new_header or "").splitlines() if l.strip()]
    present = {l.split(":", 1)[0].strip().lower() for l in lines if ":" in l}
    for old_line in (old_header or "").splitlines():
        if ":" not in old_line:
            continue
        key = old_line.split(":", 1)[0].strip()
        if key in protected and key.lower() not in present:
            lines.append(old_line)
            present.add(key.lower())
    return "\n".join(lines).rstrip()

def _coerce_email_document_content(existing: str, incoming: str) -> str:
    """Keep email docs in the To/Subject/---/body shape even if a model writes
    only the body or dumps header labels without the separator."""
    import re as _re
    old = existing or ""
    new = (incoming or "").strip()
    old_header, old_body = _split_email_header_body(old)
    _, old_history = _split_email_reply_history(old_body)
    if "\n---\n" in new:
        new_header, new_body = _split_email_header_body(new)
        new_own, new_history = _split_email_reply_history(new_body)
        if old_history and not new_history:
            new_body = (new_own + "\n\n" + old_history).strip()
        return _merge_email_headers(old_header, new_header).rstrip() + "\n---\n" + new_body
    header = old_header if old_header else "To: \nSubject: "
    if _looks_like_email_document(new):
        lines = new.splitlines()
        last_header_idx = -1
        header_re = _re.compile(r"^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder|X-Attachments):", _re.I)
        for i, line in enumerate(lines):
            if header_re.match(line.strip()):
                last_header_idx = i
        body_lines = lines[last_header_idx + 1:] if last_header_idx >= 0 else lines
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        body = "\n".join(body_lines).strip()
    else:
        body = new
    _, incoming_history = _split_email_reply_history(body)
    if old_history and not incoming_history:
        body = (body.strip() + "\n\n" + old_history).strip()
    return header.rstrip() + "\n---\n" + body

def parse_edit_blocks(content: str) -> list:
    """Parse canonical or compact FIND/REPLACE edit blocks."""
    edits = []
    # Accept the newline form used in training examples and the compact form
    # emitted by some native tool callers. Marker whitespace is structural;
    # preserve whitespace inside the actual find/replace text.
    pattern = (
        r'<<<FIND>>>[ \t]*(?:\r?\n)?(.*?)[ \t]*(?:\r?\n)?'
        r'<<<(REPLACE|REPLACE_ALL)>>>[ \t]*(?:\r?\n)?(.*?)[ \t]*(?:\r?\n)?<<<END>>>'
    )
    for m in re.finditer(pattern, content, re.DOTALL):
        edit = {"find": m.group(1), "replace": m.group(3)}
        if m.group(2) == 'REPLACE_ALL':
            edit['replace_all'] = True
        edits.append(edit)
    if not edits and "<<<FIND>>>" in content and "<<<REPLACE>>>" in content:
        # Some native callers stop generation immediately after the replace
        # body. Treat end-of-content as the terminal marker only in that
        # unmistakable two-marker form.
        compact_pattern = (
            r'<<<FIND>>>[ \t]*(?:\r?\n)?(.*?)'
            r'[ \t]*(?:\r?\n)?<<<REPLACE>>>[ \t]*(?:\r?\n)?(.*?)(?:<<<END>>>)?\s*$'
        )
        for m in re.finditer(compact_pattern, content, re.DOTALL):
            edits.append({"find": m.group(1), "replace": m.group(2)})
    return edits

def parse_suggest_blocks(content: str) -> list:
    """Parse <<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>> blocks."""
    suggestions = []
    _skip_phrases = ["no change", "fine as", "looks good", "no improvement", "keep as"]
    pattern = r'<<<FIND>>>\n(.*?)\n<<<SUGGEST>>>\n(.*?)\n<<<REASON>>>\n(.*?)\n<<<END>>>'
    for m in re.finditer(pattern, content, re.DOTALL):
        find_text = m.group(1)
        replace_text = m.group(2)
        reason = m.group(3).strip()
        # Skip no-op suggestions where find == replace or reason says no change
        if find_text.strip() == replace_text.strip():
            continue
        if any(phrase in reason.lower() for phrase in _skip_phrases):
            continue
        suggestions.append({
            "id": f"sugg-{len(suggestions)+1}",
            "find": find_text,
            "replace": replace_text,
            "reason": reason,
        })
    return suggestions


def _stable_suggestion_id(doc_id: str, suggestion: dict) -> str:
    """Deduplicate the same suggestion without colliding across tool calls."""
    payload = "\0".join((
        str(doc_id or ''), str(suggestion.get('find') or ''),
        str(suggestion.get('replace') or ''), str(suggestion.get('reason') or ''),
    ))
    return 'sugg-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _visible_text_match_source(source: str, needle: str) -> Optional[str]:
    """Return the source fragment corresponding to visible ``needle`` text.

    Rich/email documents are stored as HTML, while browser selections contain
    only rendered text. Build a lightweight visible-text index so suggestions
    can still be anchored when markup or entities sit between selected words.
    """
    if not source or not needle:
        return None

    # Keep the plain-text path cheap and exact.
    canonical_needle = needle.replace("\r\n", "\n").replace("\r", "\n")
    if canonical_needle in source:
        return canonical_needle

    if "<" not in source or ">" not in source:
        return None

    visible_chars = []
    char_spans = []
    for match in re.finditer(r"<!--.*?-->|<[^>]*>|[^<]+", source, re.DOTALL):
        token = match.group(0)
        if token.startswith("<"):
            continue
        decoded = html.unescape(token)
        # Entities decode to fewer characters; map each decoded character to
        # the source token so the returned fragment remains source-valid.
        for char in decoded:
            visible_chars.append(char)
            char_spans.append((match.start(), match.end()))

    visible = "".join(visible_chars)
    normalize = lambda value: re.sub(r"\s+", " ", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    normalized_visible = normalize(visible)
    normalized_needle = normalize(canonical_needle)
    start = normalized_visible.find(normalized_needle)
    if start < 0:
        return None

    # Map the normalized match back to source positions. Whitespace runs are
    # collapsed, so walk the original visible text while building the same
    # normalized-character spans.
    normalized_chars = []
    normalized_spans = []
    in_space = False
    for index, char in enumerate(visible_chars):
        if char.isspace():
            if not in_space:
                normalized_chars.append(" ")
                normalized_spans.append(char_spans[index])
                in_space = True
        else:
            normalized_chars.append(char)
            normalized_spans.append(char_spans[index])
            in_space = False
    while normalized_chars and normalized_chars[0].isspace():
        normalized_chars.pop(0)
        normalized_spans.pop(0)
    while normalized_chars and normalized_chars[-1].isspace():
        normalized_chars.pop()
        normalized_spans.pop()
    normalized_visible = "".join(normalized_chars)
    end = start + len(normalized_needle)
    if end > len(normalized_spans):
        return None
    source_start = normalized_spans[start][0]
    source_end = normalized_spans[end - 1][1]
    # Keep inline wrappers intact when the selection starts/ends inside one.
    # Without this, replacing a selection ending in <strong> would leave its
    # closing tag outside the replacement fragment and corrupt the HTML.
    inline_open = re.search(
        r"<(?:strong|em|b|i|u|s|del|strike|a|span|font)(?:\s[^>]*)?>$",
        source[:source_start],
        re.IGNORECASE,
    )
    if inline_open:
        source_start = inline_open.start()
    inline_close = re.match(
        r"(?:</(?:strong|em|b|i|u|s|del|strike|a|span|font)>)+",
        source[source_end:],
        re.IGNORECASE,
    )
    if inline_close:
        source_end += inline_close.end()
    return source[source_start:source_end]


def _pdf_source_upload_id(content: str) -> Optional[str]:
    try:
        from src.pdf_form_doc import find_source_upload_id
        return find_source_upload_id(content or "")
    except Exception:
        return None


def _strip_pdf_editor_markers(content: str) -> str:
    """Turn a PDF-wrapper markdown doc into ordinary editable markdown.

    PDF docs use hidden HTML comments for source-upload links, form fields, and
    page annotations. Those comments are necessary for rendering/exporting the
    original PDF, but they make a derived AI text edit keep showing the original
    PDF preview. Remove only the editor plumbing and keep the readable text.
    """
    text = content or ""
    text = re.sub(r'(?im)^\s*<!--\s*pdf(?:_form)?_source\s+[^>]*-->\s*\n*', '', text)
    text = re.sub(r'\s*<!--\s*field=[^>]*-->', '', text)
    text = re.sub(r'\s*<!--\s*annotation\s+[^>]*-->', '', text)
    return text.strip()


def _create_pdf_text_derivative(db, *, source_doc, content: str, owner: Optional[str], summary: str) -> dict:
    import uuid
    from src.database import Document, DocumentVersion

    clean = _strip_pdf_editor_markers(content)
    title_base = (getattr(source_doc, "title", None) or "PDF").strip()
    title = title_base if title_base.lower().endswith("edited") else f"{title_base} edited"
    doc_id = str(uuid.uuid4())
    ver_id = str(uuid.uuid4())
    new_doc = Document(
        id=doc_id,
        session_id=getattr(source_doc, "session_id", None),
        title=title,
        language="markdown",
        current_content=clean,
        version_count=1,
        is_active=True,
        owner=owner if owner is not None else getattr(source_doc, "owner", None),
    )
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        version_number=1,
        content=clean,
        summary=summary,
        source="ai",
    )
    db.add(new_doc)
    db.add(ver)
    db.commit()
    set_active_document(doc_id)
    return {
        "action": "create",
        "doc_id": doc_id,
        "title": title,
        "language": "markdown",
        "content": clean,
        "version": 1,
        "source_doc_id": getattr(source_doc, "id", None),
    }


class CreateDocumentTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Create a new document. Supports two formats:
        1) Line-based: line 1 = title, line 2 (optional) = language, rest = content
        2) XML-like tags: <title>...</title><language>...</language><content>...</content>
        Some models mix them — strip any XML-style tags and fall back to line parsing."""
        import uuid, re as _re
        from src.database import SessionLocal, Document, DocumentVersion, Session as DbSession

        raw = content or ""
        session_id = ctx.get("session_id")
        owner = ctx.get("owner")

        # Known languages the editor understands (match the <select> in HTML)
        _KNOWN_LANGS = {
            "python", "javascript", "typescript", "html", "css", "richtext", "markdown", "json",
            "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp", "xml", "toml",
            "ini", "ruby", "php", "csv", "email", "text", "plain", "svg",
        }

        # Try XML tag extraction first
        title = None
        language = None
        content = None
        mt = _re.search(r"<title>\s*(.*?)\s*</title>", raw, _re.DOTALL | _re.IGNORECASE)
        ml = _re.search(r"<language>\s*(.*?)\s*</language>", raw, _re.DOTALL | _re.IGNORECASE)
        mc = _re.search(r"<content>\s*(.*?)\s*</content>", raw, _re.DOTALL | _re.IGNORECASE)
        if mt or mc:
            title = mt.group(1).strip() if mt else None
            language = ml.group(1).strip().lower() if ml else None
            content = mc.group(1) if mc else None

        # Fall back to line-based parsing. First strip any stray XML-ish tags.
        if title is None or content is None:
            cleaned = _re.sub(r"</?(?:title|language|content)>", "", raw)
            lines = cleaned.strip().split("\n")
            if title is None:
                title = lines[0].strip() if lines else "Untitled"
                lines = lines[1:]
            # Only consume second line as language if it looks like a valid short lang token
            if language is None and lines:
                candidate = lines[0].strip().lower()
                if candidate and len(candidate) < 20 and " " not in candidate and candidate in _KNOWN_LANGS:
                    language = candidate
                    lines = lines[1:]
            if content is None:
                content = "\n".join(lines)

        # Validate language: must be in known set, else default based on content
        if language and language not in _KNOWN_LANGS:
            language = None
        if not language:
            # No explicit language — sniff it from the content so an SVG / HTML / JSON
            # / code document isn't silently saved as markdown. Prose → markdown.
            language = _sniff_doc_language(content)
        if _looks_like_email_document(content, title):
            language = "email"

        if not title:
            title = "Untitled"

        if not session_id:
            return {"error": "No session context for document creation"}

        db = SessionLocal()
        try:
            doc_id = str(uuid.uuid4())
            ver_id = str(uuid.uuid4())

            # Inherit ownership from the chat session so the doc survives that
            # session later being deleted (session_id → NULL).
            _sess = db.query(DbSession).filter(DbSession.id == session_id).first()
            if owner is not None and (not _sess or _sess.owner != owner):
                return {"error": "Cannot create document in another user's session"}
            _owner = _sess.owner if _sess else None

            missing_id = _missing_document_upload(_owner, content)
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }

            doc = Document(
                id=doc_id,
                session_id=session_id,
                title=title,
                language=language,
                current_content=content,
                version_count=1,
                is_active=True,
                owner=_owner,
            )
            ver = DocumentVersion(
                id=ver_id,
                document_id=doc_id,
                version_number=1,
                content=content,
                summary=f"Created by {_active_model or 'AI'}",
                source="ai",
            )
            db.add(doc)
            db.add(ver)
            db.commit()

            set_active_document(doc_id)
            try:
                from src.event_bus import fire_event
                fire_event("document_created", _owner)
            except Exception:
                logger.debug("document_created event dispatch failed", exc_info=True)

            return {
                "action": "create",
                "doc_id": doc_id,
                "title": title,
                "language": language,
                "content": content,
                "version": 1,
            }
        except Exception as e:
            db.rollback()
            return {"error": f"Failed to create document: {e}"}
        finally:
            db.close()

class UpdateDocumentTool:    
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Update an existing document. Content = full new document text."""
        import uuid
        from src.database import SessionLocal, Document, DocumentVersion

        target_id = ctx.get("doc_id", None) or _active_document_id
        owner = ctx.get("owner")

        db = SessionLocal()
        try:
            doc = None
            if target_id:
                doc = _get_owned_document(db, Document, target_id, owner)
            if (
                not doc
                and target_id
                and ctx.get("expected_document_version") is not None
            ):
                return _approved_document_version_error(None, ctx)
            if not doc:
                if target_id:
                    return {"error": "Requested document not found; no other document was changed", "exit_code": 1}
                doc = _most_recent_owned_document(db, Document, owner)
                if doc:
                    target_id = doc.id
                    set_active_document(target_id)
                    logger.info(f"update_document: fell back to most recent doc id={target_id}")
            if not doc:
                return {"error": "No documents exist to update"}

            version_error = _approved_document_version_error(doc, ctx)
            if version_error:
                return version_error

            is_email_doc = doc.language == "email" or _looks_like_email_document(doc.current_content or "", doc.title or "")
            new_content = _coerce_email_document_content(doc.current_content or "", content) if is_email_doc else content.strip()
            if is_email_doc:
                doc.language = "email"

            if new_content == (doc.current_content or ""):
                return {
                    "error": "No update applied — replacement content is unchanged",
                    "exit_code": 1,
                }

            missing_id = _missing_document_upload(owner, new_content)
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }

            if not is_email_doc and _pdf_source_upload_id(doc.current_content or ""):
                return _create_pdf_text_derivative(
                    db,
                    source_doc=doc,
                    content=new_content,
                    owner=owner,
                    summary=f"Created from PDF edit by {_active_model or 'AI'}",
                )

            new_ver = doc.version_count + 1
            ver = DocumentVersion(
                id=str(uuid.uuid4()),
                document_id=target_id,
                version_number=new_ver,
                content=new_content,
                summary=f"Updated by {_active_model or 'AI'}",
                source="ai",
            )
            doc.current_content = new_content
            doc.version_count = new_ver
            db.add(ver)
            db.commit()

            return {
                "action": "update",
                "doc_id": target_id,
                "title": doc.title,
                "language": doc.language,
                "content": new_content,
                "version": new_ver,
            }
        except Exception as e:
            db.rollback()
            return {"error": f"Failed to update document: {e}"}
        finally:
            db.close()

def _document_find_contexts(content, find):
    """Give a failed caller exact contextual anchors instead of a blind retry."""
    contexts = []
    for index, match in enumerate(re.finditer(re.escape(find), content)):
        if index >= 3:
            break
        start = content.rfind('\n', 0, match.start()) + 1
        end = content.find('\n', match.end())
        end = len(content) if end < 0 else end
        # Rich-text paragraphs are often stored on one HTML line.
        for tag in ('p', 'div'):
            paragraph = content.rfind(f'<{tag}', 0, match.start())
            close = content.find(f'</{tag}>', match.end())
            if paragraph >= 0 and close >= 0 and close + len(tag) + 3 - paragraph <= 1200:
                start, end = paragraph, close + len(tag) + 3
                break
        context = content[start:end]
        if len(context) <= 1200 and content.count(context) == 1:
            contexts.append(context)
    return '\nExact unique anchors from the current document:\n' + '\n'.join(contexts) if contexts else ''


def _document_find_repair_hint(content, find, count):
    """Offer bounded exact source text for an unmatched or ambiguous edit."""
    if isinstance(count, int) and count > 1:
        return _document_find_contexts(content, find)[:900]
    if not isinstance(count, int) or count != 0:
        return ''
    # Minified markup may be one enormous line. Parse tag boundaries instead
    # of comparing a small FIND against that entire line. This is evidence for
    # a corrected call, never permission to apply a fuzzy replacement.
    if find.lstrip().startswith('<'):
        from html.parser import HTMLParser

        class SourceTags(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=False)
                self.tags = []

            def handle_starttag(self, tag, attrs):
                raw = self.get_starttag_text()
                if raw and len(raw) <= 1000:
                    self.tags.append(raw)

            def handle_startendtag(self, tag, attrs):
                self.handle_starttag(tag, attrs)

        parser = SourceTags()
        parser.feed(content)
        matches = difflib.get_close_matches(find, list(dict.fromkeys(parser.tags)), n=2, cutoff=0.7)
        if matches:
            return 'Copy an exact source fragment into FIND (including its spacing and quotes): ' + ' | '.join(
                repr(match) for match in matches)
    if re.fullmatch(r"[\w'-]{3,40}", find):
        words = re.findall(r"[\w'-]{3,40}", content)
        by_lower = {word.casefold(): word for word in words}
        matches = difflib.get_close_matches(find.casefold(), by_lower, n=5, cutoff=0.6)
        if matches:
            return 'Closest words actually in the document: ' + ', '.join(
                repr(by_lower[word]) for word in matches)
    paragraphs = re.findall(r'<(?:p|div)\b[^>]*>.*?</(?:p|div)>', content, re.S | re.I)
    if not paragraphs:
        paragraphs = content.splitlines()
    candidates = [p for p in paragraphs if len(p) <= 500]
    matches = difflib.get_close_matches(find, candidates, n=2, cutoff=0.4)
    return 'Closest exact passages in the document: ' + ' | '.join(matches) if matches else ''


class EditDocumentTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Apply targeted FIND/REPLACE edits to an existing document."""
        import uuid
        from src.database import SessionLocal, Document, DocumentVersion

        target_id = ctx.get("doc_id", None) or _active_document_id
        owner = ctx.get("owner")

        edits = parse_edit_blocks(content)
        if not edits:
            return {"error": "No valid <<<FIND>>>...<<<REPLACE>>>...<<<END>>> blocks found"}

        db = SessionLocal()
        try:
            doc = None
            if target_id:
                doc = _get_owned_document(db, Document, target_id, owner)
            if (
                not doc
                and target_id
                and ctx.get("expected_document_version") is not None
            ):
                return _approved_document_version_error(None, ctx)
            if not doc:
                if target_id:
                    return {"error": "Requested document not found; no other document was changed", "exit_code": 1}
                # Fallback: most recently updated document. Avoids "no active doc" errors
                # after server restart or when the agent loses track of which doc to edit.
                doc = _most_recent_owned_document(db, Document, owner)
                if doc:
                    target_id = doc.id
                    set_active_document(target_id)
                    logger.info(f"edit_document: fell back to most recent doc id={target_id} title={doc.title!r}")
            if not doc:
                return {"error": "No documents exist to edit"}

            version_error = _approved_document_version_error(doc, ctx)
            if version_error:
                return version_error

            is_email_doc = doc.language == "email" or _looks_like_email_document(doc.current_content or "", doc.title or "")
            blank_find_edits = [e for e in edits if not (e.get("find") or "").strip()]
            if blank_find_edits:
                if is_email_doc:
                    replacement_body = (blank_find_edits[0].get("replace") or "").strip()
                    if not replacement_body:
                        return {"error": "No edits applied — blank FIND block had no replacement text"}
                    updated_content = _coerce_email_document_content(doc.current_content or "", replacement_body)
                    applied = 1
                    skipped = max(0, len(edits) - 1)
                    doc.language = "email"
                    missing_id = _missing_document_upload(owner, updated_content)
                    if missing_id:
                        return {
                            "error": f"Referenced upload is no longer available: {missing_id}",
                            "exit_code": 1,
                        }
                    new_ver = doc.version_count + 1
                    ver = DocumentVersion(
                        id=str(uuid.uuid4()),
                        document_id=target_id,
                        version_number=new_ver,
                        content=updated_content,
                        summary=f"Edited email body by {_active_model or 'AI'}",
                        source="ai",
                    )
                    doc.current_content = updated_content
                    doc.version_count = new_ver
                    db.add(ver)
                    db.commit()
                    return {
                        "action": "edit",
                        "doc_id": target_id,
                        "title": doc.title,
                        "language": doc.language,
                        "content": updated_content,
                        "version": new_ver,
                        "applied": applied,
                        "skipped": skipped,
                    }
                return {"error": "No edits applied — FIND text cannot be blank"}

            updated_content = doc.current_content
            applied, skipped, no_op_edits = 0, 0, 0
            invalid_edits = []
            # Validate against evolving content before the database write.
            # Only exact unique matches may be saved; report every rejected
            # entry explicitly so a partial batch cannot masquerade as complete.
            prose = str(doc.language or '').lower() in {'text', 'markdown', 'richtext', 'email', ''}
            for edit_number, edit in enumerate(edits, 1):
                find = edit['find']
                replacement = edit['replace']
                if find == replacement:
                    skipped += 1
                    no_op_edits += 1
                    continue
                if find not in updated_content:
                    stripped = "\n".join(re.sub(r"^\d+\t", "", line) for line in find.split("\n"))
                    if stripped != find and stripped in updated_content:
                        find = stripped
                count = updated_content.count(find) if find else 0
                replace_all = edit.get('replace_all') is True
                if count == 0 or (count != 1 and not replace_all):
                    invalid_edits.append((edit_number, count, edit['find']))
                    continue
                position = updated_content.index(find)
                positions = [m.start() for m in re.finditer(re.escape(find), updated_content)] if replace_all else [position]
                if prose and re.fullmatch(r"[\w]+", find):
                    for match_pos in positions:
                        before = updated_content[match_pos - 1:match_pos] if match_pos else ''
                        after = updated_content[match_pos + len(find):match_pos + len(find) + 1]
                        if (before and (before.isalnum() or before == '_')) or (after and (after.isalnum() or after == '_')):
                            invalid_edits.append((edit_number, 'part of a word', edit['find']))
                            break
                    if invalid_edits and invalid_edits[-1][0] == edit_number:
                        continue
                updated_content = updated_content.replace(find, replacement) if replace_all else updated_content[:position] + replacement + updated_content[position + len(find):]
                applied += 1

            partial_edits = bool(invalid_edits and applied)
            if invalid_edits and not partial_edits:
                details = '; '.join(
                    f'#{number} ({reason} matches): {find[:100]!r}'
                    if isinstance(reason, int) else f'#{number} ({reason}): {find[:100]!r}'
                    for number, reason, find in invalid_edits[:8]
                )
                extra = f'; and {len(invalid_edits) - 8} more' if len(invalid_edits) > 8 else ''
                return {
                    'error': f'No edits applied. Invalid FIND entries: {details}{extra}. '
                             'Do not repeat the unchanged call. Copy FIND exactly from the current source or the hints below, '
                             'then retry the corrected entries. If no hint identifies the target, read the document first. '
                             'Other entries were not saved. ' + ' '.join(
                                 f'#{number}: {_document_find_repair_hint(doc.current_content, find, reason)}'
                                 for number, reason, find in invalid_edits[:3]
                                 if _document_find_repair_hint(doc.current_content, find, reason)
                             ),
                    'exit_code': 1, 'applied': 0,
                    'invalid_edit_numbers': [number for number, _, _ in invalid_edits],
                }

            if applied == 0:
                if no_op_edits == len(edits):
                    return {"error": "No edits applied: every FIND and REPLACE pair is identical. Write a changed replacement that fulfills the requested revision; keep FIND copied from the current document."}
                return {"error": f"No edits applied — none of the FIND blocks matched the document content (skipped {skipped})"}

            missing_id = _missing_document_upload(owner, updated_content)
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }

            if _pdf_source_upload_id(doc.current_content or ""):
                return _create_pdf_text_derivative(
                    db,
                    source_doc=doc,
                    content=updated_content,
                    owner=owner,
                    summary=f"Created from PDF edit by {_active_model or 'AI'} ({applied} edit(s))",
                )

            new_ver = doc.version_count + 1
            ver = DocumentVersion(
                id=str(uuid.uuid4()),
                document_id=target_id,
                version_number=new_ver,
                content=updated_content,
                summary=f"Edited by {_active_model or 'AI'} ({applied} edit(s))",
                source="ai",
            )
            doc.current_content = updated_content
            doc.version_count = new_ver
            db.add(ver)
            db.commit()

            result = {
                "action": "edit",
                "doc_id": target_id,
                "title": doc.title,
                "language": doc.language,
                "content": updated_content,
                "version": new_ver,
                "applied": applied,
                "skipped": skipped,
            }
            if partial_edits:
                result.update({
                    'partial': True,
                    'rejected': len(invalid_edits),
                    'invalid_edits': [
                        {'number': number, 'matches': reason, 'find': find[:100],
                         'hint': _document_find_repair_hint(updated_content, find, reason)}
                        for number, reason, find in invalid_edits
                    ],
                })
            return result
        except Exception as e:
            db.rollback()
            return {"error": f"Failed to edit document: {e}"}
        finally:
            db.close()

class SuggestDocumentTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Create inline suggestions for the active document WITHOUT modifying it."""
        from src.database import SessionLocal, Document

        target_id = ctx.get("doc_id", None) or _active_document_id
        owner = ctx.get("owner")

        if not target_id:
            return {"error": "No active document to suggest on"}

        suggestions = parse_suggest_blocks(content)
        if not suggestions:
            return {"error": "No valid <<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>> blocks found"}

        db = SessionLocal()
        try:
            doc = _get_owned_document(db, Document, target_id, owner)
            if not doc:
                return {"error": f"Document {target_id} not found"}

            version_error = _approved_document_version_error(doc, ctx)
            if version_error:
                return version_error

            # Validate that FIND text exists in document
            valid, invalid = [], []
            for number, s in enumerate(suggestions, 1):
                find_text = s["find"]
                # Browser selections from markdown, rich text, and email are
                # rendered text, while the stored document may contain LF
                # normalization or HTML markup. Resolve the visible passage
                # back to the exact source fragment used by the editor.
                source_find = _visible_text_match_source(doc.current_content, find_text)
                if source_find is not None:
                    stored = doc.current_content or ''
                    if stored.count(source_find) != 1:
                        invalid.append({'number': number, 'find': find_text[:100],
                                        'reason': 'ambiguous',
                                        'hint': _document_find_contexts(stored, source_find)[:900]})
                        continue
                    if re.fullmatch(r"[\w]+", source_find):
                        pos = stored.index(source_find)
                        before = stored[pos - 1:pos] if pos else ''
                        after = stored[pos + len(source_find):pos + len(source_find) + 1]
                        if (before and before.isalnum()) or (after and after.isalnum()):
                            invalid.append({'number': number, 'find': find_text[:100],
                                            'reason': 'part of a word', 'hint': ''})
                            continue
                    if source_find != find_text:
                        s = dict(s)
                        s["find"] = source_find
                    s["id"] = _stable_suggestion_id(target_id, s)
                    valid.append(s)
                else:
                    invalid.append({'number': number, 'find': find_text[:100],
                                    'reason': 'not found',
                                    'hint': _document_find_repair_hint(doc.current_content or '', find_text, 0)[:900]})

            if not valid:
                details = '; '.join(f"#{item['number']} {item['reason']}: {item['find']!r} {item['hint']}"
                                    for item in invalid[:5])
                return {'error': 'No suggestions created: ' + details,
                        'exit_code': 1, 'rejected': len(invalid)}

            return {
                "action": "suggest",
                "doc_id": target_id,
                "suggestions": valid,
                "count": len(valid),
                "partial": bool(invalid),
                "rejected": len(invalid),
                "invalid_suggestions": invalid,
            }
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Document management tool (delete, list, organize)
# ---------------------------------------------------------------------------
class ManageDocumentTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        """Manage documents: list, read/view/open, delete, tidy.

        Output format mirrors `manage_session`: list rows include a
        clickable `[Title](#document-<id>)` anchor + relative timestamps
        so the user can click straight from chat to open the editor.
        """
        from core.database import SessionLocal, Document
        from datetime import datetime, timezone

        owner = ctx.get("owner")

        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = args.get("action", "list")
        if action in {"search", "find"}:
            action = "list"
            if not args.get("search"):
                args["search"] = args.get("text") or args.get("query") or args.get("title")
        db = SessionLocal()

        def _rel(ts):
            if not ts:
                return 'never'
            try:
                now = datetime.now(timezone.utc) if ts.tzinfo is not None else datetime.utcnow()
                diff = (now - ts).total_seconds()
            except Exception:
                return 'unknown'
            if diff < 60: return 'just now'
            if diff < 3600: return f'{int(diff / 60)}m ago'
            if diff < 86400: return f'{int(diff / 3600)}h ago'
            if diff < 86400 * 7: return f'{int(diff / 86400)}d ago'
            return ts.strftime('%Y-%m-%d')

        try:
            if action == "list":
                q = db.query(Document).filter(Document.is_active == True)
                q = _owned_document_query(q, Document, owner)
                search_text = args.get("search")
                if search_text:
                    # Tolerate unambiguous conversational framing in a title
                    # fallback without introducing broad fuzzy matching.
                    search_text = re.sub(
                        r"\s+from\s+my\s+documents\b", "", str(search_text), flags=re.IGNORECASE
                    )
                    search_text = re.sub(
                        r"\s+(?:instead|please)\s*$", "", search_text, flags=re.IGNORECASE
                    ).strip()
                if args.get("language"):
                    q = q.filter(Document.language == args["language"])
                requested_limit = args.get("limit", 50)
                try:
                    requested_limit = max(1, min(int(requested_limit), 200))
                except (TypeError, ValueError):
                    requested_limit = 50
                q = q.order_by(Document.updated_at.desc())
                # A plain listing must not load the entire document library
                # (including every document body) before applying its limit.
                if not search_text:
                    q = q.limit(requested_limit)
                docs = q.all()
                if search_text:
                    docs = _rank_document_search(docs, search_text)
                docs = docs[:requested_limit]
                if not docs:
                    msg = "No documents found" + (f" matching '{search_text}'" if search_text else "") + "."
                    return {"response": msg, "documents": [], "exit_code": 0}
                lines = []
                items = []
                for i, d in enumerate(docs):
                    size = len(d.current_content or "")
                    lang = d.language or "text"
                    ts = getattr(d, 'updated_at', None) or getattr(d, 'created_at', None)
                    marker = " ← most recent" if i == 0 else ""
                    lines.append(
                        f"- [{d.title}](#document-{d.id}) — {lang}, {size} chars, updated {_rel(ts)}{marker}"
                    )
                    items.append({"id": d.id, "title": d.title, "language": lang, "size": size})
                header = f"Found {len(docs)} document(s), sorted most-recent first. Click a title to open:"
                return {
                    "response": header + "\n" + "\n".join(lines),
                    "documents": items,
                    "exit_code": 0,
                }

            elif action in ("read", "view", "open", "get"):
                doc_id = args.get("document_id") or args.get("id") or args.get("uid")
                if not doc_id:
                    return {"error": "Need document_id (use action=list to find one)", "exit_code": 1}
                doc = _get_owned_document(db, Document, doc_id, owner, active_only=True)
                if not doc:
                    return {"error": f"Document '{doc_id}' not found", "exit_code": 1}
                set_active_document(doc.id)
                body = doc.current_content or ""
                try:
                    preview_limit = max(1, min(int(args.get("limit", MAX_READ_CHARS)), MAX_READ_CHARS))
                except (TypeError, ValueError):
                    preview_limit = MAX_READ_CHARS
                try:
                    offset = max(0, int(args.get("offset", 0) or 0))
                except (TypeError, ValueError):
                    offset = 0
                offset = min(offset, len(body))
                end = min(offset + preview_limit, len(body))
                truncated = end < len(body)
                preview = body[offset:end]
                if truncated:
                    preview += f"\n... (truncated, {len(body)} chars total; next_offset={end})"
                anchor = f"[{doc.title}](#document-{doc.id})"
                return {
                    "response": f"{anchor} — click to open in editor.\n\n```{doc.language or ''}\n{preview}\n```",
                    "document": {
                        "id": doc.id,
                        "title": doc.title,
                        "language": doc.language,
                        "size": len(body),
                        "content": preview,
                        "truncated": truncated,
                        "offset": offset,
                        "next_offset": end if truncated else None,
                    },
                    "exit_code": 0,
                }

            elif action == "delete":
                doc_id = args.get("document_id") or args.get("id") or args.get("uid") or ctx.get("doc_id") or _active_document_id
                doc = None
                if doc_id:
                    doc = _get_owned_document(db, Document, doc_id, owner)
                if not doc:
                    if doc_id:
                        return {"error": "Requested document not found; no other document was deleted", "exit_code": 1}
                    # Fallback: most recently updated doc (likely what the user means)
                    doc = _most_recent_owned_document(db, Document, owner, active_only=True)
                if not doc:
                    return {"error": "No document to delete", "exit_code": 1}
                version_error = _approved_document_version_error(doc, ctx)
                if version_error:
                    return version_error
                title = doc.title
                doc.is_active = False
                db.commit()
                if _active_document_id == doc.id:
                    set_active_document(None)
                return {"response": f"Deleted document '{title}'", "exit_code": 0}

            elif action == "tidy":
                from src.document_actions import run_document_tidy
                result = await run_document_tidy(owner or "")
                return {"response": result, "exit_code": 0}

            else:
                return {"error": f"Unknown action: {action}", "exit_code": 1}
        except Exception as e:
            logger.error(f"manage_documents error: {e}")
            return {"error": str(e), "exit_code": 1}
        finally:
            db.close()
