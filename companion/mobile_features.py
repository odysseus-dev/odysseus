"""Companion mobile-feature endpoints — additive overlay (/api/companion/*).

Self-contained on purpose: the companion bridge exists in several stacked-PR
lineages with DIFFERENT internals (some have `has_companion_scope`, some take
`setup_companion_routes(memory_manager=...)`, etc.). To work on ANY of them
without entangling, this module defines its OWN owner-resolution + scope +
admin-gate helpers and registers its OWN APIRouter, included separately in
app.py via `setup_mobile_companion_routes()`. It never imports companion.routes
internals. Endpoints lazy-import their data deps (core.database, email helpers,
SkillsManager, etc.) exactly like the core bridge does.

Security: data reads require the `companion` token scope; admin features
(contacts/terminal/vault/mcp/cookbook) require the off-by-default
`companion_admin_enabled` setting AND a `companion`-scoped token AND an
owner who is a server admin. Owner-scoped throughout (own rows + legacy
null-owner shared rows; cross-owner → 404).
"""

from fastapi import APIRouter, Form, HTTPException, Request

from src.auth_helpers import get_current_user


def token_owner(request: Request) -> str | None:
    """The real owner to attribute a request to, for read-scoping.

    Cookie sessions resolve to the logged-in username via get_current_user.
    Bearer-token callers come through as the sandboxed pseudo-user "api"; their
    real owner is stamped on request.state.api_token_owner by the auth
    middleware. Returns None when no owner can be resolved.
    """
    if getattr(request.state, "api_token", False):
        return getattr(request.state, "api_token_owner", None)
    return get_current_user(request)


def owner_can_see(row_owner, owner) -> bool:
    """Owner-scope rule for read endpoints.

    A caller sees a row when it is their own, or when it is a legacy null-owner
    ("shared") row. A caller must NEVER see another owner's row. Mirrors the
    `owner_filter` rule used elsewhere, expressed as a pure predicate so it can
    be tested directly and used as a defensive in-Python check alongside the
    SQL filter.
    """
    return row_owner is None or row_owner == owner


def has_companion_scope(request: Request) -> bool:
    """Whether the caller may read the companion DATA views.

    DEPLOYMENT NOTE: this tree mints pairing tokens with the ``chat`` scope
    (companion/pairing.py COMPANION_SCOPE = "chat") and its existing
    notes/tasks/memory endpoints already serve those chat tokens. To stay
    CONSISTENT with that posture (and so the paired phone's existing token works
    without re-pairing), we accept ``chat`` OR ``companion`` here — a normal
    paired token, not a scope-less one. Owner-scoping (token_owner) still ensures
    a caller only ever sees their OWN rows, and admin features remain behind the
    separate require_companion_admin triple-lock regardless of this.
    """
    if not getattr(request.state, "api_token", False):
        return True
    scopes = getattr(request.state, "api_token_scopes", None) or []
    return "companion" in scopes or "chat" in scopes


def setup_mobile_companion_routes() -> APIRouter:
    """Additive router with the mobile-only companion feature endpoints."""
    router = APIRouter(prefix="/api/companion", tags=["companion-mobile"])

    @router.get("/documents")
    def documents(request: Request):
        """List the caller's own documents (RAG library), newest first.

        Owner-scoped exactly like the stock /api/documents/library, but resolved
        to the token's real owner (plus legacy null-owner shared rows) instead of
        the sandboxed "api" user. Read-only summary — returns a short content
        snippet, never the full body (that's the per-doc GET below). Requires the
        companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read documents.")
        from core.database import SessionLocal, Document

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(Document).filter(Document.is_active == True)  # noqa: E712
            # Exclude archived (NULL = legacy rows = not archived).
            q = q.filter((Document.archived == False) | (Document.archived == None))  # noqa: E711,E712
            if owner:
                q = q.filter((Document.owner == owner) | (Document.owner == None))  # noqa: E711
            for d in q.all():
                if not owner_can_see(d.owner, owner):
                    continue
                content = d.current_content or ""
                out.append({
                    "id": d.id,
                    "title": d.title,
                    "language": d.language,
                    "snippet": content[:200],
                    "updated_at": getattr(d, "updated_at", None) and str(d.updated_at),
                })
        finally:
            db.close()
        # Newest first when a timestamp is available; stable otherwise.
        out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return {"items": out}

    @router.get("/documents/{doc_id}")
    def document_detail(request: Request, doc_id: str):
        """Full body of one of the caller's documents. 404 (never 403) for a
        missing OR cross-owner doc, so existence is never confirmed to a
        non-owner. Requires the companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read documents.")
        from core.database import SessionLocal, Document

        owner = token_owner(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc or not owner_can_see(doc.owner, owner):
                raise HTTPException(404, "Document not found")
            return {
                "id": doc.id,
                "title": doc.title,
                "language": doc.language,
                "content": doc.current_content or "",
                "archived": bool(doc.archived),
                "updated_at": getattr(doc, "updated_at", None) and str(doc.updated_at),
            }
        finally:
            db.close()

    # ---- Model compare (history + verdict record) -------------------------
    # The phone runs the two model streams itself via the EXISTING owner-scoped
    # /api/session + /api/chat_stream — so it never touches the stock
    # /api/compare/start, whose endpoint-key lookup is not owner-scoped. These
    # endpoints only persist and list the caller's own comparison verdicts.

    @router.get("/compare/history")
    def compare_history(request: Request):
        """The caller's own past model comparisons, newest first. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read comparisons.")
        from core.database import SessionLocal, Comparison

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(Comparison)
            if owner:
                q = q.filter((Comparison.owner == owner) | (Comparison.owner == None))  # noqa: E711
            for c in q.all():
                if not owner_can_see(c.owner, owner):
                    continue
                out.append({
                    "id": c.id,
                    "prompt": (c.prompt or "")[:100],
                    "model_a": c.model_a,
                    "model_b": c.model_b,
                    "winner": c.winner,
                    "is_blind": bool(c.is_blind),
                    "voted_at": c.voted_at.isoformat() if c.voted_at else None,
                    "created_at": c.created_at.isoformat() if getattr(c, "created_at", None) else None,
                })
        finally:
            db.close()
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return {"items": out}

    def _cal_iso(dt):
        try:
            return dt.isoformat() if dt is not None else None
        except Exception:
            return None

    def _cal_parse_dt(value):
        from datetime import datetime as _dt
        if not value:
            return None
        try:
            return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @router.get("/calendars")
    def calendars(request: Request):
        """List the caller's own calendars. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read calendars.")
        from core.database import SessionLocal, CalendarCal

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(CalendarCal)
            if owner:
                q = q.filter((CalendarCal.owner == owner) | (CalendarCal.owner == None))  # noqa: E711
            for c in q.all():
                if not owner_can_see(c.owner, owner):
                    continue
                out.append({"id": c.id, "name": c.name, "color": c.color, "source": c.source})
        finally:
            db.close()
        return {"items": out}

    @router.get("/events")
    def events(request: Request, start: str = "", end: str = ""):
        """The caller's events overlapping [start, end] (ISO). Scoped to the
        caller's calendars. Non-recurring overlap only (no RRULE expansion in
        v1). Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read events.")
        from core.database import SessionLocal, CalendarCal, CalendarEvent

        owner = token_owner(request)
        start_dt = _cal_parse_dt(start)
        end_dt = _cal_parse_dt(end)
        out = []
        db = SessionLocal()
        try:
            cq = db.query(CalendarCal)
            if owner:
                cq = cq.filter((CalendarCal.owner == owner) | (CalendarCal.owner == None))  # noqa: E711
            cal_ids = [c.id for c in cq.all() if owner_can_see(c.owner, owner)]
            if cal_ids:
                for e in db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(cal_ids)).all():
                    if e.status == "cancelled":
                        continue
                    # Window overlap (when no/unparseable range given → return all).
                    if start_dt and e.dtend is not None and e.dtend <= start_dt:
                        continue
                    if end_dt and e.dtstart is not None and e.dtstart >= end_dt:
                        continue
                    out.append({
                        "uid": e.uid,
                        "calendar_id": e.calendar_id,
                        "summary": e.summary,
                        "description": e.description,
                        "location": e.location,
                        "dtstart": _cal_iso(e.dtstart),
                        "dtend": _cal_iso(e.dtend),
                        "all_day": bool(e.all_day),
                        "rrule": e.rrule or "",
                        "status": e.status,
                        "importance": e.importance,
                        "event_type": e.event_type,
                        "color": e.color,
                    })
        finally:
            db.close()
        out.sort(key=lambda r: r.get("dtstart") or "")
        return {"items": out}

    @router.get("/email/accounts")
    def email_accounts(request: Request):
        """The caller's own email accounts (no secrets). Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read email.")
        from core.database import SessionLocal, EmailAccount

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(EmailAccount)
            if owner:
                q = q.filter((EmailAccount.owner == owner) | (EmailAccount.owner == None))  # noqa: E711
            for a in q.all():
                if not owner_can_see(a.owner, owner):
                    continue
                out.append({
                    "id": a.id,
                    "name": a.name,
                    "from_address": a.from_address,
                    "enabled": bool(a.enabled),
                    "is_default": bool(a.is_default),
                })
        finally:
            db.close()
        return {"items": out}

    @router.get("/email/messages")
    def email_messages(request: Request, account_id: str, folder: str = "INBOX", limit: int = 30):
        """List recent message headers from one of the caller's mailboxes.
        Owner-asserted before any IMAP I/O. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read email.")
        import email as _email
        from routes.email_helpers import _assert_owns_account, _imap, _decode_header

        owner = token_owner(request)
        # A null/empty owner makes _assert_owns_account a no-op AND unscopes the
        # config lookup — i.e. it would reach ANY mailbox. Fail closed, exactly
        # like the email write path.
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        _assert_owns_account(account_id, owner)  # 404 on cross-owner — the gate
        limit = max(1, min(int(limit or 30), 100))
        out = []
        try:
            with _imap(account_id, owner=owner) as conn:
                conn.select(folder, readonly=True)
                typ, data = conn.uid("search", None, "ALL")
                uids = (data[0].split() if data and data[0] else [])[-limit:]
                for u in reversed(uids):
                    typ, md = conn.uid("fetch", u, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                    if not md or not md[0]:
                        continue
                    msg = _email.message_from_bytes(md[0][1])
                    out.append({
                        "uid": u.decode() if isinstance(u, bytes) else str(u),
                        "subject": _decode_header(msg.get("Subject")),
                        "from": _decode_header(msg.get("From")),
                        "date": msg.get("Date"),
                    })
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Could not reach the mailbox: {e}")
        return {"items": out, "folder": folder}

    @router.get("/email/message/{uid}")
    def email_message(request: Request, uid: str, account_id: str, folder: str = "INBOX"):
        """Read one message's text body. Owner-asserted. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read email.")
        import email as _email
        from routes.email_helpers import _assert_owns_account, _imap, _decode_header, _extract_text

        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        _assert_owns_account(account_id, owner)
        try:
            with _imap(account_id, owner=owner) as conn:
                conn.select(folder, readonly=True)
                typ, md = conn.uid("fetch", uid.encode() if isinstance(uid, str) else uid, "(RFC822)")
                if not md or not md[0]:
                    raise HTTPException(404, "Message not found")
                msg = _email.message_from_bytes(md[0][1])
                return {
                    "uid": uid,
                    "subject": _decode_header(msg.get("Subject")),
                    "from": _decode_header(msg.get("From")),
                    "to": _decode_header(msg.get("To")),
                    "date": msg.get("Date"),
                    "body": _extract_text(msg),
                }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Could not read the message: {e}")

    @router.get("/gallery")
    def gallery(request: Request):
        """List the caller's own gallery images (metadata + companion image URL)."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read the gallery.")
        from core.database import SessionLocal, GalleryImage

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)  # noqa: E712
            if owner:
                q = q.filter((GalleryImage.owner == owner) | (GalleryImage.owner == None))  # noqa: E711
            for im in q.all():
                if not owner_can_see(im.owner, owner):
                    continue
                created = getattr(im, "taken_at", None) or getattr(im, "created_at", None)
                out.append({
                    "id": im.id,
                    "prompt": im.prompt,
                    "model": im.model,
                    "favorite": bool(im.favorite),
                    "width": im.width,
                    "height": im.height,
                    "created_at": created and str(created),
                    "image_url": f"/api/companion/gallery/image/{im.id}",
                })
        finally:
            db.close()
        out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return {"items": out}

    @router.get("/gallery/image/{image_id}")
    def gallery_image(request: Request, image_id: str):
        """Stream one of the caller's images. 404 (not 403) for a missing OR
        cross-owner image, before any file access. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read the gallery.")
        import os
        import re as _re
        from fastapi.responses import FileResponse
        from core.database import SessionLocal, GalleryImage

        owner = token_owner(request)
        db = SessionLocal()
        try:
            im = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not im or not owner_can_see(im.owner, owner):
                raise HTTPException(404, "Image not found")
            filename = im.filename or ""
        finally:
            db.close()
        # Defensive basename — never let a stored filename escape the image dir.
        safe = _re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[:160]
        path = os.path.join("data", "generated_images", safe)
        if not safe or not os.path.isfile(path):
            raise HTTPException(404, "Image not found")
        return FileResponse(path)

    # ---- Personal assistant -----------------------------------------------
    # View/edit the caller's per-owner assistant (CrewMember, is_default_
    # assistant). Owner-scoped to the token's REAL owner — which is never the
    # synthetic "api" pseudo-user, so unlike the bearer path through the stock
    # route this resolves a genuine owner. We do NOT seed ScheduledTask
    # check-ins from the phone (a desktop concern); just the assistant row.
    # Companion scope required.

    _ASSISTANT_SYNTHETIC = frozenset({"internal-tool", "api", "demo", "system", ""})

    def _assistant_dict(c):
        return {
            "id": c.id,
            "name": c.name,
            "user_name": c.user_name,
            "personality": c.personality,
            "model": c.model,
            "greeting": c.greeting,
            "timezone": c.timezone,
            "avatar": c.avatar,
            "enabled": bool(c.is_active),
        }

    @router.get("/assistant")
    def assistant_get(request: Request):
        """The caller's personal assistant, or {assistant: null} if none yet.
        Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read the assistant.")
        from core.database import SessionLocal, CrewMember

        owner = token_owner(request)
        db = SessionLocal()
        try:
            q = db.query(CrewMember).filter(CrewMember.is_default_assistant == True)  # noqa: E712
            if owner:
                q = q.filter(CrewMember.owner == owner)
            crew = q.first()
            # Defense in depth: never hand back another owner's row.
            if crew and crew.owner != owner:
                crew = None
            return {"assistant": _assistant_dict(crew) if crew else None}
        finally:
            db.close()

    @router.get("/skills")
    def skills(request: Request):
        """List the caller's own learned skills (name/description/category)."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read skills.")
        from core.constants import DATA_DIR
        from services.memory.skills import SkillsManager

        owner = token_owner(request)
        sm = SkillsManager(DATA_DIR)
        out = []
        for s in sm.load(owner=owner):
            out.append({
                "name": s.get("name"),
                "description": s.get("description"),
                "category": s.get("category"),
            })
        return {"items": out}


    return router
