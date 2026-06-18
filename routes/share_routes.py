"""Public, read-only share links for chat sessions and document artifacts.

`POST/DELETE /api/share*` are owner-scoped (normal auth). The public render at
`GET /share/{token}` is auth-exempt (see AUTH_EXEMPT in app.py): the random
token in the URL is the credential. Anyone with the link gets a self-contained,
read-only HTML page — no app shell, no write paths.
"""
import html
import logging
import secrets
import uuid

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from core.database import SessionLocal, ShareToken, Document, Session as DbSession
from src.auth_helpers import get_current_user
from routes.session_routes import _content_to_text, _verify_session_owner
from routes.document_helpers import _verify_doc_owner

logger = logging.getLogger(__name__)

_VALID_TYPES = {"session", "document"}


def _render_markdown(md_text: str) -> str:
    """Markdown → sanitized HTML (falls back to escaped <pre> on any error)."""
    try:
        from src.visual_report import _md_to_html
        return _md_to_html(md_text or "")
    except Exception:
        return "<pre>" + html.escape(md_text or "") + "</pre>"


_PAGE_CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  margin:0;background:#fafafa;color:#18181b;line-height:1.6}
@media (prefers-color-scheme:dark){body{background:#0a0a0a;color:#e4e4e7}
  .msg{background:#18181b!important;border-color:#27272a!important}
  .bar{background:#0a0a0acc!important;border-color:#27272a!important}
  pre{background:#000!important}code{background:#27272a}}
.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;
  gap:1rem;padding:.7rem 1rem;background:#fafafacc;backdrop-filter:blur(8px);border-bottom:1px solid #e4e4e7}
.brand{font-weight:600;font-size:.95rem;letter-spacing:-.01em}
.ro{font-size:.75rem;color:#71717a;border:1px solid currentColor;border-radius:999px;padding:.1rem .55rem;opacity:.7}
.wrap{max-width:768px;margin:0 auto;padding:1.5rem 1rem 4rem}
h1.title{font-size:1.5rem;font-weight:650;letter-spacing:-.02em;margin:.5rem 0 1.5rem}
.msg{margin:.85rem 0;padding:.9rem 1.1rem;border-radius:12px;border:1px solid #e4e4e7;background:#fff}
.msg.user{background:#f4f4f5}
.role{font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#71717a;margin-bottom:.35rem}
.msg :first-child{margin-top:0}.msg :last-child{margin-bottom:0}
pre{background:#f4f4f5;padding:.75rem;border-radius:8px;overflow-x:auto;font-size:.85em}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.9em}
pre code{background:none;padding:0}
img{max-width:100%;height:auto}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #e4e4e7;padding:.4rem .6rem;text-align:left}
.art{width:100%;height:80vh;border:1px solid #e4e4e7;border-radius:12px;background:#fff}
.foot{text-align:center;color:#a1a1aa;font-size:.78rem;margin-top:2.5rem}
.empty{color:#71717a;text-align:center;padding:3rem 1rem}
"""


def _page(title: str, body: str, *, status: int = 200) -> HTMLResponse:
    safe_title = html.escape(title or "Shared from Odysseus")
    doc = (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<title>{safe_title}</title><style>{_PAGE_CSS}</style></head><body>"
        "<div class='bar'><span class='brand'>Odysseus</span>"
        "<span class='ro'>Read-only · shared</span></div>"
        f"<div class='wrap'>{body}"
        "<div class='foot'>Shared from Odysseus</div></div></body></html>"
    )
    return HTMLResponse(content=doc, status_code=status)


def _render_session(session_manager, sid: str) -> HTMLResponse:
    try:
        session = session_manager.get_session(sid)
    except (KeyError, Exception):
        session = None
    if session is None:
        return _page("Unavailable", "<div class='empty'>This conversation is no longer available.</div>", status=404)
    title = html.escape(getattr(session, "name", None) or "Shared conversation")
    parts = [f"<h1 class='title'>{title}</h1>"]
    rendered = 0
    for m in getattr(session, "history", []) or []:
        role = getattr(m, "role", "") or ""
        if role not in ("user", "assistant"):
            continue
        text = _content_to_text(getattr(m, "content", ""))
        if not text.strip():
            continue
        rendered += 1
        if role == "user":
            inner = html.escape(text).replace("\n", "<br>")
        else:
            inner = _render_markdown(text)
        label = "You" if role == "user" else "Odysseus"
        parts.append(f"<div class='msg {role}'><div class='role'>{label}</div>{inner}</div>")
    if rendered == 0:
        parts.append("<div class='empty'>This conversation has no messages to show.</div>")
    return _page(getattr(session, "name", None) or "Shared conversation", "".join(parts))


def _looks_like_markup(content: str, lang: str) -> bool:
    if lang in ("html", "svg", "xml"):
        return True
    head = (content or "").lstrip()[:200].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or head.startswith("<svg")


def _render_document(doc_id: str) -> HTMLResponse:
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
    finally:
        db.close()
    if doc is None:
        return _page("Unavailable", "<div class='empty'>This document is no longer available.</div>", status=404)
    title = html.escape(doc.title or "Shared document")
    content = doc.current_content or ""
    lang = (doc.language or "").lower()
    head = f"<h1 class='title'>{title}</h1>"
    if _looks_like_markup(content, lang):
        # Render the artifact isolated in a sandboxed iframe so its scripts run
        # but can't touch this (same-origin) page. No allow-same-origin → opaque
        # origin. srcdoc is permitted under the share CSP's frame-src 'self'.
        srcdoc = html.escape(content, quote=True)
        body = head + f"<iframe class='art' sandbox='allow-scripts allow-popups' srcdoc=\"{srcdoc}\"></iframe>"
    elif lang in ("markdown", "md", ""):
        body = head + _render_markdown(content)
    else:
        body = head + "<pre><code>" + html.escape(content) + "</code></pre>"
    return _page(doc.title or "Shared document", body)


def setup_share_routes(session_manager):
    router = APIRouter(tags=["share"])

    def _owner(request: Request) -> str:
        # In auth-disabled single-user mode get_current_user is None; collapse to
        # a stable sentinel so the (owner, resource) row is consistent.
        return get_current_user(request) or "local"

    # ── Management API (auth required) ──────────────────────────────────────
    @router.post("/api/share")
    async def create_share(request: Request, body: dict):
        rtype = (body or {}).get("resource_type")
        rid = (body or {}).get("resource_id")
        if rtype not in _VALID_TYPES or not rid:
            raise HTTPException(400, "resource_type must be 'session' or 'document' and resource_id is required")
        # Ownership: reuse the existing per-resource verifiers (raise 404 if not owner).
        if rtype == "session":
            _verify_session_owner(request, rid, session_manager)
        else:
            user = get_current_user(request)
            db = SessionLocal()
            try:
                doc = db.query(Document).filter(Document.id == rid).first()
                if doc is None:
                    raise HTTPException(404, "Document not found")
                _verify_doc_owner(db, doc, user)
            finally:
                db.close()
        owner = _owner(request)
        db = SessionLocal()
        try:
            existing = (
                db.query(ShareToken)
                .filter(ShareToken.owner == owner, ShareToken.resource_type == rtype, ShareToken.resource_id == str(rid))
                .first()
            )
            if existing:
                token = existing.token
            else:
                token = secrets.token_urlsafe(16)
                db.add(ShareToken(id=str(uuid.uuid4()), token=token, owner=owner, resource_type=rtype, resource_id=str(rid)))
                db.commit()
        finally:
            db.close()
        return {"token": token, "path": f"/share/{token}"}

    @router.get("/api/share/lookup")
    async def lookup_share(request: Request, resource_type: str, resource_id: str):
        if resource_type not in _VALID_TYPES or not resource_id:
            raise HTTPException(400, "invalid resource")
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = (
                db.query(ShareToken)
                .filter(ShareToken.owner == owner, ShareToken.resource_type == resource_type, ShareToken.resource_id == str(resource_id))
                .first()
            )
            if not row:
                return {"token": None, "path": None}
            return {"token": row.token, "path": f"/share/{row.token}"}
        finally:
            db.close()

    @router.delete("/api/share/{token}")
    async def revoke_share(request: Request, token: str):
        owner = _owner(request)
        db = SessionLocal()
        try:
            row = db.query(ShareToken).filter(ShareToken.token == token).first()
            if row is None:
                return {"ok": True}  # already gone — idempotent
            if row.owner != owner:
                raise HTTPException(404, "Not found")
            db.delete(row)
            db.commit()
        finally:
            db.close()
        return {"ok": True}

    # ── Public render (auth-exempt) ─────────────────────────────────────────
    @router.get("/share/{token}")
    async def view_share(token: str):
        db = SessionLocal()
        try:
            row = db.query(ShareToken).filter(ShareToken.token == token).first()
        finally:
            db.close()
        if row is None:
            return _page("Not found", "<div class='empty'>This link is invalid or has been revoked.</div>", status=404)
        if row.resource_type == "session":
            return _render_session(session_manager, row.resource_id)
        return _render_document(row.resource_id)

    return router
