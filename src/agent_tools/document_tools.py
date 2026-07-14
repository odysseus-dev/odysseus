from typing import Any, Dict, List, Optional
import logging
import re
import json
from src.constants import MAX_READ_CHARS

logger = logging.getLogger(__name__)

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

def _coerce_email_document_content(existing: str, incoming: str) -> str:
    """Keep email docs in the To/Subject/---/body shape even if a model writes
    only the body or dumps header labels without the separator."""
    import re as _re
    old = existing or ""
    new = (incoming or "").strip()
    if "\n---\n" in new:
        return new
    header = old.split("\n---\n", 1)[0] if "\n---\n" in old else "To: \nSubject: "
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
    return header.rstrip() + "\n---\n" + body

def _parse_tool_args(content):
    """Parse a tool-call argument blob.

    Accepts either a JSON string or an already-decoded dict. Unwraps the
    common `{"body": {...}}` envelope that smaller models emit when they
    read tool descriptions like "Body is JSON: {...}" literally — they
    pass `body` as a field name rather than treating it as a noun.

    Returns a dict on success, raises ValueError on bad JSON.
    """
    if isinstance(content, str):
        try:
            args = json.loads(content) if content.strip() else {}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(str(e))
    elif isinstance(content, dict):
        args = content
    else:
        args = {}
    # Unwrap {"body": {...}} envelope — but only if `body` is the sole key
    # and points at a dict. We don't want to clobber a legitimate `body`
    # field on tools where it's a real arg (e.g. send_email body text).
    if (
        isinstance(args, dict)
        and len(args) == 1
        and "body" in args
        and isinstance(args["body"], dict)
        and "action" in args["body"]  # extra safety: only unwrap if the inner dict looks like a tool call
    ):
        args = args["body"]
    return args

def parse_edit_blocks(content: str) -> list:
    """Parse <<<FIND>>>...<<<REPLACE>>>...<<<END>>> blocks."""
    edits = []
    pattern = r'<<<FIND>>>\n(.*?)\n<<<REPLACE>>>\n(.*?)\n<<<END>>>'
    for m in re.finditer(pattern, content, re.DOTALL):
        edits.append({"find": m.group(1), "replace": m.group(2)})
    return edits

def parse_suggest_blocks(content: str) -> list:
    """Parse <<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>> blocks."""
    suggestions = []
    _skip_phrases = ["no change", "clear", "fine as", "looks good", "no improvement", "keep as"]
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
            "python", "javascript", "typescript", "html", "css", "markdown", "json",
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
            if not doc:
                doc = _most_recent_owned_document(db, Document, owner)
                if doc:
                    target_id = doc.id
                    set_active_document(target_id)
                    logger.info(f"update_document: fell back to most recent doc id={target_id}")
            if not doc:
                return {"error": "No documents exist to update"}

            is_email_doc = doc.language == "email" or _looks_like_email_document(doc.current_content or "", doc.title or "")
            new_content = _coerce_email_document_content(doc.current_content or "", content) if is_email_doc else content.strip()
            if is_email_doc:
                doc.language = "email"

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
            if not doc:
                # Fallback: most recently updated document. Avoids "no active doc" errors
                # after server restart or when the agent loses track of which doc to edit.
                doc = _most_recent_owned_document(db, Document, owner)
                if doc:
                    target_id = doc.id
                    set_active_document(target_id)
                    logger.info(f"edit_document: fell back to most recent doc id={target_id} title={doc.title!r}")
            if not doc:
                return {"error": "No documents exist to edit"}

            updated_content = doc.current_content
            applied = 0
            skipped = 0
            for edit in edits:
                _find = edit["find"]
                if _find in updated_content:
                    updated_content = updated_content.replace(_find, edit["replace"], 1)
                    applied += 1
                else:
                    # Defensive: the active-doc context shows a "N\t" line-number
                    # gutter for reference. Weaker models sometimes copy that prefix
                    # into FIND. If the exact match failed, retry with a leading
                    # "<digits><tab>" stripped from each FIND line — but only use it
                    # when that stripped form actually matches, so we never corrupt a
                    # legitimately tab-prefixed document.
                    _stripped = "\n".join(re.sub(r"^\d+\t", "", _l) for _l in _find.split("\n"))
                    if _stripped != _find and _stripped in updated_content:
                        updated_content = updated_content.replace(_stripped, edit["replace"], 1)
                        applied += 1
                        logger.info("edit_document: matched after stripping line-number gutter from FIND")
                    else:
                        logger.warning(f"edit_document: FIND text not found, skipping: {_find[:80]!r}")
                        skipped += 1

            if applied == 0:
                return {"error": f"No edits applied — none of the FIND blocks matched the document content (skipped {skipped})"}

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

            # Validate that FIND text exists in document
            valid = []
            for s in suggestions:
                if s["find"] in doc.current_content:
                    valid.append(s)
                else:
                    logger.warning(f"suggest_document: FIND text not found, skipping: {s['find'][:80]!r}")

            if not valid:
                return {"error": "No suggestions matched the document content"}

            return {
                "action": "suggest",
                "doc_id": target_id,
                "suggestions": valid,
                "count": len(valid),
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
                if args.get("search"):
                    q = q.filter(Document.title.ilike(f"%{args['search']}%"))
                if args.get("language"):
                    q = q.filter(Document.language == args["language"])
                docs = q.order_by(Document.updated_at.desc()).limit(args.get("limit", 50)).all()
                if not docs:
                    msg = "No documents found" + (f" matching '{args['search']}'" if args.get("search") else "") + "."
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
                body = doc.current_content or ""
                preview_limit = int(args.get("limit", MAX_READ_CHARS))
                truncated = len(body) > preview_limit
                preview = body[:preview_limit] + (f"\n... (truncated, {len(body)} chars total)" if truncated else "")
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
                    },
                    "exit_code": 0,
                }

            elif action == "delete":
                doc_id = args.get("document_id") or args.get("id") or args.get("uid") or _active_document_id
                doc = None
                if doc_id:
                    doc = _get_owned_document(db, Document, doc_id, owner)
                if not doc:
                    # Fallback: most recently updated doc (likely what the user means)
                    doc = _most_recent_owned_document(db, Document, owner, active_only=True)
                if not doc:
                    return {"error": "No document to delete", "exit_code": 1}
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