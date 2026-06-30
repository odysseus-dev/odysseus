"""document_helpers.py — Pydantic models, doc serializers, owner gating, file-locator helpers shared with document_routes.py."""

"""Document routes — CRUD for living documents with version history."""

import logging
import os
import re
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from core.database import Document, DocumentVersion
from core.database import Session as DbSession
from src.auth_helpers import _auth_disabled
from src.upload_handler import UploadHandler

logger = logging.getLogger(__name__)


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
        if _auth_disabled():
            return  # Single-user / no-auth mode: allow access
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
    we therefore match the owner strictly for authenticated callers."""
    if not user:
        if user == "" or _auth_disabled():
            return q
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


def _resolve_user_upload_path(
    upload_handler: Any,
    upload_id: str,
    owner: Optional[str],
    auth_manager=None,
) -> Optional[str]:
    """Resolve an upload id to a filesystem path the caller may read."""
    if upload_handler is None:
        return None
    resolved = upload_handler.resolve_upload(
        upload_id,
        owner=owner,
        auth_manager=auth_manager,
    )
    if not isinstance(resolved, dict) or not resolved:
        return None
    path = resolved.get("path")
    upload_dir = getattr(upload_handler, "upload_dir", None)
    if path and upload_dir and not _upload_path_inside(upload_dir, path):
        logger.warning("Upload path outside upload directory: %s", path)
        return None
    return path


def _locate_upload(
    upload_dir: str,
    file_id: str,
    owner: Optional[str] = None,
    auth_manager=None,
    upload_handler: Any = None,
):
    """Find an upload by its filename ID via UploadHandler.resolve_upload."""
    if upload_handler is None:
        from src.upload_handler import UploadHandler

        base_dir = os.path.dirname(os.path.abspath(upload_dir))
        upload_handler = UploadHandler(base_dir, upload_dir)
    return _resolve_user_upload_path(upload_handler, file_id, owner, auth_manager)


def _assert_pdf_marker_upload_owned(
    request: Request,
    content: str,
    user: Optional[str],
    upload_handler: Any,
) -> None:
    """Reject document content whose pdf_source marker points at another user's upload."""
    if upload_handler is None:
        return
    from src.pdf_form_doc import find_source_upload_id

    upload_id = find_source_upload_id(content or "")
    if not upload_id:
        return
    auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
    if not _resolve_user_upload_path(upload_handler, upload_id, user, auth_manager):
        raise HTTPException(
            400,
            "Document PDF marker references an upload you do not own",
        )


def _derive_title(content: str) -> str:
    """Derive a title from document content."""
    import re
    if not isinstance(content, str):
        return "Untitled"
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
