"""document_helpers.py — Pydantic models, doc serializers, owner gating, file-locator helpers shared with document_routes.py."""

"""Document routes — CRUD for living documents with version history."""

import logging
import os
import re
from typing import Dict, Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from core.database import Document, DocumentVersion
from core.database import Session as DbSession

logger = logging.getLogger(__name__)
_UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{32}\.[A-Za-z0-9]+$")


# ---- Request schemas ----

class DocumentCreate(BaseModel):
    session_id: Optional[str] = None
    title: str = "Untitled"
    language: Optional[str] = None
    content: str = ""

class DocumentUpdate(BaseModel):
    content: str
    summary: Optional[str] = None

class DocumentPatch(BaseModel):
    title: Optional[str] = None
    language: Optional[str] = None
    session_id: Optional[str] = None  # link/unlink document to a session


# ---- Helpers ----

def _doc_to_dict(doc: Document) -> Dict[str, Any]:
    return {
        "id": doc.id,
        "session_id": doc.session_id,
        "title": doc.title,
        "language": doc.language,
        "current_content": doc.current_content,
        "version_count": doc.version_count,
        "is_active": doc.is_active,
        "archived": bool(getattr(doc, "archived", False)),
        "created_at": (doc.created_at.isoformat() + "Z") if doc.created_at else None,
        "updated_at": (doc.updated_at.isoformat() + "Z") if doc.updated_at else None,
        # Source-email provenance (set when doc was created from an email
        # attachment) — drives the "Send signed reply" menu item.
        "source_email_uid":        getattr(doc, "source_email_uid", None),
        "source_email_folder":     getattr(doc, "source_email_folder", None),
        "source_email_account_id": getattr(doc, "source_email_account_id", None),
        "source_email_message_id": getattr(doc, "source_email_message_id", None),
    }

def _version_to_dict(v: DocumentVersion) -> Dict[str, Any]:
    return {
        "id": v.id,
        "document_id": v.document_id,
        "version_number": v.version_number,
        "content": v.content,
        "summary": v.summary,
        "source": v.source,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _verify_doc_owner(db, doc: Document, user: str):
    """Verify `user` owns this document. Raise 404 if not.

    Documents now carry their own `owner` column, so a doc whose session
    was deleted (session_id → NULL) can still prove ownership and stay
    openable / cloneable. We trust that column first and only fall back to
    the session join for any not-yet-backfilled legacy row.
    """
    if user is None:
        raise HTTPException(403, "Authentication required")
    if doc.owner is not None:
        if doc.owner != user:
            raise HTTPException(404, "Document not found")
        return
    # Legacy fallback: derive ownership from the linked session.
    if not doc.session_id:
        raise HTTPException(404, "Document not found")
    session = db.query(DbSession).filter(DbSession.id == doc.session_id).first()
    if not session or session.owner != user:
        raise HTTPException(404, "Document not found")


def _owner_session_filter(q, user):
    """Restrict a documents query to those owned by `user`.

    Documents now carry their own `owner` column (backfilled at boot from
    the linked session, or assigned to the admin user for legacy/orphaned
    docs). We filter on that directly rather than on a session join, so a
    document whose session was deleted (session_id → NULL) still shows up
    for its owner instead of silently vanishing from the Library + search.

    The owner backfill runs in init_db before the app serves requests, so
    by the time this filter is live there are no NULL-owner rows to leak;
    we therefore match the owner strictly."""
    if user is None:
        return q.filter(False)
    return q.filter(Document.owner == user)



def _slug(name: str) -> str:
    """Filesystem-friendly version of a document title.

    Whitespace becomes underscores; other unsafe punctuation is dropped.
    Preserves letters, digits, dot, hyphen, underscore. Idempotent.
    """
    import re as _re
    s = (name or "").strip()
    # Drop the trailing extension if the title happens to include one
    s = _re.sub(r'\.pdf$', '', s, flags=_re.IGNORECASE)
    s = _re.sub(r'\s+', '_', s)
    s = _re.sub(r'[^A-Za-z0-9._-]', '', s)
    s = _re.sub(r'_+', '_', s).strip('_')
    return s or "form"


# DPI scale for the interactive PDF view. ~150 DPI (2x of 72 PDF user-units).
_PDF_RENDER_SCALE = 2.0


def _upload_path_inside(upload_dir: str, path: str) -> bool:
    base = os.path.realpath(upload_dir)
    p = os.path.realpath(path)
    try:
        return os.path.commonpath([base, p]) == base
    except Exception:
        return False


def _upload_owner_allowed(
    meta: Optional[dict],
    user: Optional[str],
    auth_manager=None,
    allow_admin: bool = True,
) -> bool:
    if not user:
        return (
            not bool(auth_manager and getattr(auth_manager, "is_configured", False))
            and not (meta and meta.get("owner") is not None)
        )
    if allow_admin and auth_manager and hasattr(auth_manager, "is_admin"):
        try:
            if auth_manager.is_admin(user):
                return True
        except Exception:
            pass
    return bool(meta and meta.get("owner") == user)


def _locate_upload(upload_dir: str, file_id: str, owner: Optional[str] = None, auth_manager=None):
    """Find an upload by its filename ID.

    Lookup order:
      1. The `uploads.json` index that `UploadHandler.save_upload` maintains,
         so owner can be verified before a document reads the source file.
      2. Direct hit at `upload_dir/file_id` (very small deployments).
      3. Fallback: `os.walk` the date-bucketed tree. Slow on large stores;
         only allowed after the index owner check passes, or in single-user /
         admin-style contexts where no owner is enforced.

    `followlinks=False` keeps a stray symlink loop in `data/uploads/` from
    spinning the walker into infinite recursion.
    """
    import json as _json

    if not _UPLOAD_ID_RE.fullmatch(file_id or ""):
        logger.warning("Rejected invalid upload id in document lookup: %r", file_id)
        return None

    meta = None
    try:
        idx_path = os.path.join(upload_dir, "uploads.json")
        if os.path.exists(idx_path):
            with open(idx_path, "r", encoding="utf-8") as f:
                idx = _json.load(f)
            for item in (idx.values() if isinstance(idx, dict) else []):
                if isinstance(item, dict) and item.get("id") == file_id:
                    meta = item
                    break
    except Exception:
        meta = None

    if not _upload_owner_allowed(meta, owner, auth_manager):
        logger.warning("Upload %s denied for document owner %s", file_id, owner)
        return None

    if meta:
        p = meta.get("path")
        if p and os.path.exists(p) and _upload_path_inside(upload_dir, p):
            return p

    direct = os.path.join(upload_dir, file_id)
    if os.path.exists(direct) and _upload_path_inside(upload_dir, direct):
        return direct

    for root, _dirs, files in os.walk(upload_dir, followlinks=False):
        if file_id in files:
            p = os.path.join(root, file_id)
            if _upload_path_inside(upload_dir, p):
                return p
    return None


def _derive_title(content: str) -> str:
    """Derive a title from document content."""
    import re
    text = content.strip()
    if not text:
        return "Untitled"

    # Markdown header
    md = re.match(r'^#{1,3}\s+(.+)', text, re.MULTILINE)
    if md:
        title = md.group(1).strip()
        if len(title) > 50:
            title = title[:48] + "…"
        return title

    # HTML heading
    html = re.search(r'<h[1-3][^>]*>([^<]+)</h[1-3]>', text, re.IGNORECASE)
    if html:
        title = html.group(1).strip()
        if len(title) > 50:
            title = title[:48] + "…"
        return title

    # First non-empty line (if short enough)
    for line in text.split('\n'):
        line = line.strip()
        if line and 2 <= len(line) <= 60:
            title = re.sub(r'[:#*`]+$', '', line).strip()
            if title and len(title) > 50:
                title = title[:48] + "…"
            return title or "Untitled"

    return "Untitled"
