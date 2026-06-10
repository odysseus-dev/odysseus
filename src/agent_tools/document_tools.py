import uuid
import logging
import re

from typing import Any, Dict, List, Optional

async def update_document(content: str, doc_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Update an existing document. Content = full new document text."""
    from src.database import SessionLocal, Document, DocumentVersion
    from src import tool_implementations
    from src.tool_implementations import (
        get_active_document,
        _get_owned_document,
        _most_recent_owned_document,
        set_active_document,
        _looks_like_email_document,
        _coerce_email_document_content,
    )

    target_id = doc_id or get_active_document()

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

async def create_document(content_block: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Create a new document. Supports two formats:
      1) Line-based: line 1 = title, line 2 (optional) = language, rest = content
      2) XML-like tags: <title>...</title><language>...</language><content>...</content>
    Some models mix them — strip any XML-style tags and fall back to line parsing."""
    from src.database import SessionLocal, Document, DocumentVersion, Session as DbSession
    
    raw = content_block or ""

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
