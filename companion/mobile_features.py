"""Companion mobile-feature endpoints — additive overlay (/api/companion/*).

Self-contained on purpose: the companion bridge exists in several stacked-PR
lineages with DIFFERENT internals (some have `has_companion_scope`, some take
`setup_companion_routes(memory_manager=...)`, etc.). To work on ANY of them
without entangling, this module defines its OWN owner-resolution + scope +
admin-gate helpers and registers its OWN APIRouter, included separately in
app.py via `setup_mobile_companion_routes()`. It never imports companion.routes
internals. Endpoints lazy-import their data deps (core.database, email helpers,
SkillsManager, etc.) exactly like the core bridge does.

Security: these read endpoints require a paired (`chat`/`companion`) token scope
and are owner-scoped throughout — own rows + legacy null-owner shared rows;
cross-owner → 404, never confirming a row's existence to a non-owner. List
endpoints are paged (bounded ?limit/?offset) so a caller can't pull a whole
table in one request.

Deliberately absent: admin-privileged tools (shell/terminal, vault, MCP,
cookbook, contacts). A paired device is narrow integration access and must not
inherit an admin account's capabilities; anything of that class belongs behind
its own opt-in, discussed on its own terms, not bundled into this bridge.
"""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from src.auth_helpers import get_current_user

# List endpoints page their results so a caller can never pull a whole table in
# one request (a DoS / bandwidth / row-count-disclosure surface). Callers may
# narrow or walk with ?limit / ?offset; limit is clamped to a bounded window.
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 200


def _page(limit, offset):
    """Clamp caller-supplied pagination to a sane, bounded window.

    limit → [1, MAX_PAGE_LIMIT] (default DEFAULT_PAGE_LIMIT); offset → >= 0.
    Bad/garbage values fall back to the defaults rather than erroring.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE_LIMIT
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0
    return max(1, min(limit, MAX_PAGE_LIMIT)), max(0, offset)


def _scope_query(q, model, owner):
    """Apply the owner-scope rule to a SQL query so it returns EXACTLY the rows
    `owner_can_see` would keep (own rows + legacy null-owner shared rows), and no
    more. Making the SQL predicate match the in-Python check is what lets a LIMIT
    be applied in SQL without a later filter shrinking the page.
    """
    if owner:
        return q.filter((model.owner == owner) | (model.owner == None))  # noqa: E711
    # Legacy single-user / unresolved owner: only the shared null-owner rows.
    return q.filter(model.owner == None)  # noqa: E711


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
    a caller only ever sees their OWN rows, and no admin-privileged tool is
    reachable through this bridge at all.
    """
    if not getattr(request.state, "api_token", False):
        return True
    scopes = getattr(request.state, "api_token_scopes", None) or []
    return "companion" in scopes or "chat" in scopes


def setup_mobile_companion_routes(upload_handler=None, task_scheduler=None) -> APIRouter:
    """Additive router with the mobile-only companion feature endpoints."""
    router = APIRouter(prefix="/api/companion", tags=["companion-mobile"])

    @router.get("/documents")
    def documents(request: Request, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """List the caller's own documents (RAG library), paged.

        Owner-scoped exactly like the stock /api/documents/library, but resolved
        to the token's real owner (plus legacy null-owner shared rows) instead of
        the sandboxed "api" user. Read-only summary — returns a short content
        snippet, never the full body (that's the per-doc GET below). Bounded by
        ?limit/?offset. Requires the companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read documents.")
        from core.database import SessionLocal, Document

        owner = token_owner(request)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            q = db.query(Document).filter(Document.is_active == True)  # noqa: E712
            # Exclude archived (NULL = legacy rows = not archived).
            q = q.filter((Document.archived == False) | (Document.archived == None))  # noqa: E711,E712
            q = _scope_query(q, Document, owner)
            # Document has no timestamp column — page deterministically by id.
            q = q.order_by(Document.id).offset(offset).limit(limit)
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
        return {"items": out, "limit": limit, "offset": offset}

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
    def compare_history(request: Request, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """The caller's own past model comparisons, most-recently-voted first.
        Bounded by ?limit/?offset. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read comparisons.")
        from core.database import SessionLocal, Comparison

        owner = token_owner(request)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            q = _scope_query(db.query(Comparison), Comparison, owner)
            # voted_at is the only timestamp; id is a stable tiebreaker for paging.
            q = q.order_by(Comparison.voted_at.desc(), Comparison.id).offset(offset).limit(limit)
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
        return {"items": out, "limit": limit, "offset": offset}

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
    def calendars(request: Request, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """List the caller's own calendars, paged. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read calendars.")
        from core.database import SessionLocal, CalendarCal

        owner = token_owner(request)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            q = _scope_query(db.query(CalendarCal), CalendarCal, owner)
            q = q.order_by(CalendarCal.id).offset(offset).limit(limit)
            for c in q.all():
                if not owner_can_see(c.owner, owner):
                    continue
                out.append({"id": c.id, "name": c.name, "color": c.color, "source": c.source})
        finally:
            db.close()
        return {"items": out, "limit": limit, "offset": offset}

    @router.get("/events")
    def events(request: Request, start: str = "", end: str = "",
               limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """The caller's events overlapping [start, end] (ISO), scoped to the
        caller's calendars, ordered by start and bounded by ?limit/?offset. The
        window and cancelled-status filters are applied in SQL (no full-table
        fetch). Non-recurring overlap only (no RRULE expansion in v1). Companion
        scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read events.")
        from core.database import SessionLocal, CalendarCal, CalendarEvent

        owner = token_owner(request)
        start_dt = _cal_parse_dt(start)
        end_dt = _cal_parse_dt(end)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            cq = _scope_query(db.query(CalendarCal), CalendarCal, owner)
            cal_ids = [c.id for c in cq.all() if owner_can_see(c.owner, owner)]
            if cal_ids:
                eq = db.query(CalendarEvent).filter(CalendarEvent.calendar_id.in_(cal_ids))
                # Exclude cancelled (NULL status = legacy = not cancelled).
                eq = eq.filter((CalendarEvent.status != "cancelled") | (CalendarEvent.status == None))  # noqa: E711
                # Window overlap pushed into SQL: keep events that end after the
                # window start AND begin before the window end (when a bound is given).
                if start_dt:
                    eq = eq.filter(CalendarEvent.dtend > start_dt)
                if end_dt:
                    eq = eq.filter(CalendarEvent.dtstart < end_dt)
                eq = eq.order_by(CalendarEvent.dtstart).offset(offset).limit(limit)
                for e in eq.all():
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
        return {"items": out, "limit": limit, "offset": offset}

    @router.get("/email/accounts")
    def email_accounts(request: Request, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """The caller's own email accounts (no secrets), paged. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read email.")
        from core.database import SessionLocal, EmailAccount

        owner = token_owner(request)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            q = _scope_query(db.query(EmailAccount), EmailAccount, owner)
            q = q.order_by(EmailAccount.id).offset(offset).limit(limit)
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
        return {"items": out, "limit": limit, "offset": offset}

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
    def gallery(request: Request, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0):
        """List the caller's own gallery images (metadata + companion image URL),
        newest first, bounded by ?limit/?offset."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read the gallery.")
        from core.database import SessionLocal, GalleryImage

        owner = token_owner(request)
        limit, offset = _page(limit, offset)
        out = []
        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.is_active == True)  # noqa: E712
            q = _scope_query(q, GalleryImage, owner)
            # taken_at is the capture timestamp; id is a stable tiebreaker for paging.
            q = q.order_by(GalleryImage.taken_at.desc(), GalleryImage.id).offset(offset).limit(limit)
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
        return {"items": out, "limit": limit, "offset": offset}

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


    @router.post("/compare/record")
    def compare_record(
        request: Request,
        prompt: str = Form(...),
        model_a: str = Form(...),
        model_b: str = Form(...),
        winner: str = Form(...),        # "a", "b", or "tie"
        is_blind: str = Form("false"),
    ):
        """Persist a comparison verdict owned by the caller. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to record comparisons.")
        if winner not in ("a", "b", "tie"):
            raise HTTPException(400, "winner must be 'a', 'b', or 'tie'")
        import uuid as _uuid
        from datetime import datetime as _dt
        from core.database import SessionLocal, Comparison

        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        comp_id = str(_uuid.uuid4())
        db = SessionLocal()
        try:
            comp = Comparison(
                id=comp_id,
                prompt=(prompt or "")[:500],
                model_a=model_a,
                model_b=model_b,
                endpoint_a="",
                endpoint_b="",
                winner=winner,
                is_blind=str(is_blind).lower() == "true",
                voted_at=_dt.utcnow(),
                owner=owner,
            )
            db.add(comp)
            db.commit()
        finally:
            db.close()
        return {"id": comp_id, "status": "ok"}

    @router.delete("/compare/{comp_id}")
    def compare_delete(request: Request, comp_id: str):
        """Delete one of the caller's comparisons. Strict ownership: missing OR
        cross-owner (incl. legacy null-owner shared) → 404, never confirming
        existence to a non-owner. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to delete comparisons.")
        from core.database import SessionLocal, Comparison

        owner = token_owner(request)
        db = SessionLocal()
        try:
            comp = db.query(Comparison).filter(Comparison.id == comp_id).first()
            if not comp or comp.owner != owner:
                raise HTTPException(404, "Comparison not found")
            db.delete(comp)
            db.commit()
            return {"status": "deleted"}
        finally:
            db.close()

    def _owned_calendar(db, cal_id, owner):
        from core.database import CalendarCal
        cal = db.query(CalendarCal).filter(CalendarCal.id == cal_id).first()
        # Strict: the calendar must be the caller's own (not missing, not a
        # legacy null-owner shared row) before we let them write into it.
        if not cal or cal.owner != owner:
            raise HTTPException(404, "Calendar not found")
        return cal

    @router.post("/events")
    def create_event(
        request: Request,
        calendar_id: str = Form(...),
        summary: str = Form(...),
        dtstart: str = Form(...),
        dtend: str = Form(...),
        description: str = Form(""),
        location: str = Form(""),
        all_day: str = Form("false"),
    ):
        """Create an event in one of the caller's OWN calendars. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to create events.")
        import uuid as _uuid
        from core.database import SessionLocal, CalendarEvent

        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        start_dt = _cal_parse_dt(dtstart)
        end_dt = _cal_parse_dt(dtend)
        if start_dt is None or end_dt is None:
            raise HTTPException(400, "dtstart and dtend must be ISO datetimes")
        db = SessionLocal()
        try:
            _owned_calendar(db, calendar_id, owner)
            uid = str(_uuid.uuid4())
            ev = CalendarEvent(
                uid=uid,
                calendar_id=calendar_id,
                summary=summary,
                description=description or "",
                location=location or "",
                dtstart=start_dt,
                dtend=end_dt,
                all_day=str(all_day).lower() == "true",
                status="confirmed",
            )
            db.add(ev)
            db.commit()
            return {"uid": uid, "status": "ok"}
        finally:
            db.close()

    @router.delete("/events/{uid}")
    def delete_event(request: Request, uid: str):
        """Delete one of the caller's events. 404 (not 403) when the event is
        missing or lives in a calendar the caller doesn't own. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to delete events.")
        from core.database import SessionLocal, CalendarEvent

        owner = token_owner(request)
        db = SessionLocal()
        try:
            ev = db.query(CalendarEvent).filter(CalendarEvent.uid == uid).first()
            if not ev:
                raise HTTPException(404, "Event not found")
            # Ownership is via the event's calendar — reuse the strict gate.
            _owned_calendar(db, ev.calendar_id, owner)
            db.delete(ev)
            db.commit()
            return {"status": "deleted"}
        finally:
            db.close()

    @router.post("/email/send")
    def email_send(
        request: Request,
        account_id: str = Form(...),
        to: str = Form(...),
        subject: str = Form(""),
        body: str = Form(""),
    ):
        """Send a plain-text email from one of the caller's OWN accounts.
        Owner-asserted before creds are read. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to send email.")
        from email.mime.text import MIMEText
        from email.utils import parseaddr
        from routes.email_helpers import _assert_owns_account, _get_email_config, _send_smtp_message

        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        _assert_owns_account(account_id, owner)

        recipients = [r.strip() for r in to.replace(";", ",").split(",") if r.strip()]
        if not recipients or not all("@" in parseaddr(r)[1] for r in recipients):
            raise HTTPException(400, "Provide at least one valid recipient address.")

        cfg = _get_email_config(account_id, owner=owner)
        from_addr = cfg.get("from_address") or cfg.get("smtp_user") or ""
        if not cfg.get("smtp_host") or not from_addr:
            raise HTTPException(400, "This account has no SMTP configuration.")
        msg = MIMEText(body or "", _charset="utf-8")
        msg["Subject"] = subject or ""
        msg["From"] = from_addr
        msg["To"] = ", ".join(recipients)
        try:
            _send_smtp_message(cfg, from_addr, recipients, msg.as_string())
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Send failed: {e}")
        return {"status": "sent", "to": recipients}

    @router.patch("/assistant")
    def assistant_patch(
        request: Request,
        name: str = Form(None),
        user_name: str = Form(None),
        personality: str = Form(None),
        greeting: str = Form(None),
        model: str = Form(None),
        timezone: str = Form(None),
    ):
        """Update (creating if absent) the caller's personal assistant. Companion
        scope. Refuses synthetic/non-human owners."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to edit the assistant.")
        import uuid as _uuid
        from core.database import SessionLocal, CrewMember

        owner = token_owner(request)
        if not owner or owner in _ASSISTANT_SYNTHETIC:
            raise HTTPException(400, "Cannot edit an assistant for this token owner.")
        db = SessionLocal()
        try:
            crew = db.query(CrewMember).filter(
                CrewMember.owner == owner,
                CrewMember.is_default_assistant == True,  # noqa: E712
            ).first()
            if crew is None:
                crew = CrewMember(
                    id=str(_uuid.uuid4()),
                    owner=owner,
                    name=name or "Assistant",
                    is_default_assistant=True,
                    is_active=True,
                )
                db.add(crew)
            # Strict: never mutate a row we don't own (paranoia; query already scopes).
            elif crew.owner != owner:
                raise HTTPException(404, "Assistant not found")
            for field, value in (
                ("name", name), ("user_name", user_name), ("personality", personality),
                ("greeting", greeting), ("model", model), ("timezone", timezone),
            ):
                if value is not None:
                    setattr(crew, field, value)
            db.commit()
            db.refresh(crew)
            return {"assistant": _assistant_dict(crew)}
        finally:
            db.close()

    @router.get("/skills/{name}/markdown")
    def skill_markdown(request: Request, name: str):
        """Raw SKILL.md for one of the caller's skills. 404 for a skill the
        caller doesn't own (it isn't in their scoped list). Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read skills.")
        from core.constants import DATA_DIR
        from services.memory.skills import SkillsManager

        owner = token_owner(request)
        sm = SkillsManager(DATA_DIR)
        match = next(
            (s for s in sm.load(owner=owner) if s.get("name") == name or s.get("id") == name),
            None,
        )
        if not match:
            raise HTTPException(404, "Skill not found")
        md = sm.read_skill_md(match.get("name"), owner=owner)
        if md is None:
            raise HTTPException(404, "Skill source unavailable")
        return {"name": match.get("name"), "markdown": md}


    # ---- Task controls ----------------------------------------------------

    def _owned_task(db, task_id, owner):
        from core.database import ScheduledTask
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        # Strict: the task must be the caller's own — not missing, not another
        # owner's, not a legacy null-owner shared row. Non-owner gets 404, never
        # confirming the task exists.
        if not task or task.owner != owner:
            raise HTTPException(404, "Task not found")
        return task

    @router.post("/tasks/{task_id}/pause")
    def task_pause(request: Request, task_id: str):
        """Pause one of the caller's own scheduled tasks. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to control tasks.")
        from core.database import SessionLocal

        owner = token_owner(request)
        db = SessionLocal()
        try:
            task = _owned_task(db, task_id, owner)
            task.status = "paused"
            db.commit()
            return {"ok": True, "status": "paused"}
        finally:
            db.close()


    @router.post("/tasks/{task_id}/resume")
    def task_resume(request: Request, task_id: str):
        """Resume one of the caller's own tasks, recomputing its next run for
        schedule-triggered tasks (else next_run stays stale). Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to control tasks.")
        from core.database import SessionLocal
        from src.task_scheduler import compute_next_run

        owner = token_owner(request)
        db = SessionLocal()
        try:
            task = _owned_task(db, task_id, owner)
            task.status = "active"
            if (task.trigger_type or "schedule") == "schedule":
                task.next_run = compute_next_run(
                    task.schedule, task.scheduled_time,
                    task.scheduled_day, task.scheduled_date,
                    cron_expression=task.cron_expression,
                )
            db.commit()
            return {
                "ok": True,
                "status": "active",
                "next_run": task.next_run.isoformat() + "Z" if task.next_run else None,
            }
        finally:
            db.close()


    @router.post("/tasks/{task_id}/run")
    async def task_run(request: Request, task_id: str, force: bool = False):
        """Run one of the caller's own tasks now. Ownership is asserted before the
        scheduler is asked to dispatch it. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to control tasks.")
        if task_scheduler is None:
            raise HTTPException(503, "Task execution is unavailable.")
        from core.database import SessionLocal

        owner = token_owner(request)
        db = SessionLocal()
        try:
            _owned_task(db, task_id, owner)
        finally:
            db.close()
        started = await task_scheduler.run_task_now(task_id, force=force)
        if not started:
            raise HTTPException(409, "Task is already running")
        return {"ok": True, "status": "running"}


    @router.post("/tasks/{task_id}/stop")
    async def task_stop(request: Request, task_id: str):
        """Stop a currently-running task the caller owns. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to control tasks.")
        if task_scheduler is None:
            raise HTTPException(503, "Task execution is unavailable.")
        from core.database import SessionLocal

        owner = token_owner(request)
        db = SessionLocal()
        try:
            _owned_task(db, task_id, owner)
        finally:
            db.close()
        stopped = await task_scheduler.stop_task(task_id)
        if not stopped:
            raise HTTPException(404, "Task is not running")
        return {"ok": True, "status": "stopped"}

    # ---- Gallery favorite toggle ------------------------------------------

    @router.post("/gallery/image/{image_id}/favorite")
    def gallery_favorite(request: Request, image_id: str):
        """Toggle the favorite flag on one of the caller's own images. Strict
        ownership: cross-owner OR legacy null-owner → 404. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to edit the gallery.")
        from core.database import SessionLocal, GalleryImage

        owner = token_owner(request)
        db = SessionLocal()
        try:
            img = db.query(GalleryImage).filter(GalleryImage.id == image_id).first()
            if not img or img.owner != owner:
                raise HTTPException(404, "Image not found")
            img.favorite = not img.favorite
            db.commit()
            return {"ok": True, "favorite": img.favorite}
        finally:
            db.close()

    # ---- Email AI (summarize + draft reply) -------------------------------
    # The phone already has the message body from GET /email/message, so it posts
    # the text back rather than us re-opening IMAP. Ownership of the named account
    # is still asserted before any per-owner endpoint/key is resolved, so a caller
    # can't borrow another owner's model endpoint to run these.

    def _assert_account_owned(account_id, owner):
        """Ownership gate for the email AI actions, with a clean failure.

        routes.email_helpers._assert_owns_account raises from inside an
        `except Exception` block, so whatever it surfaces is derived from a
        caught error. Re-raise a fresh exception carrying only the status and a
        fixed message, so nothing about the failure can travel to the phone.
        """
        from routes.email_helpers import _assert_owns_account

        try:
            _assert_owns_account(account_id, owner)
        except HTTPException as exc:
            status = 404 if exc.status_code == 404 else 503
            raise HTTPException(
                status,
                "Account not found" if status == 404 else "Account check failed",
            ) from None

    async def _companion_llm(owner, system, user, max_tokens):
        import logging as _logging

        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async_with_fallback
        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url:
            url, model, headers = resolve_endpoint("default", owner=owner)
        if not url:
            # Raised before the call, so it is ours to surface verbatim.
            raise HTTPException(503, "No model endpoint configured.")
        try:
            return await llm_call_async_with_fallback(
                [(url, model, headers)],
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.4,
                max_tokens=max_tokens,
            )
        except Exception as e:  # noqa: BLE001
            # EVERY failure below this line is replaced with a generic 502 —
            # including HTTPException. The provider layer raises detail that
            # names the deployment, e.g. HTTPException(503, "Upstream
            # <host>:<port> marked unreachable"), and re-raising that would hand
            # a paired phone the server's internal endpoint host. The real error
            # is logged (exception type only, so a message carrying the failing
            # URL or request body can't reach the log either).
            _logging.getLogger(__name__).error(
                "companion email AI call failed: %s", type(e).__name__
            )
            raise HTTPException(502, "The model endpoint could not be reached.")


    @router.post("/email/summarize")
    async def email_summarize(
        request: Request,
        account_id: str = Form(...),
        subject: str = Form(""),
        body: str = Form(...),
    ):
        """Summarize a message the caller supplies, using the owner's own model
        endpoint. Account ownership is asserted first. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to summarize email.")
        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        _assert_account_owned(account_id, owner)
        if not (body or "").strip():
            raise HTTPException(400, "Nothing to summarize.")
        summary = await _companion_llm(
            owner,
            "You summarize emails into 2-4 concise bullet points. Output only the bullets.",
            f"Subject: {subject}\n\n{body[:12000]}",
            400,
        )
        return {"summary": (summary or "").strip()}


    @router.post("/email/ai-reply")
    async def email_ai_reply(
        request: Request,
        account_id: str = Form(...),
        original_body: str = Form(...),
        subject: str = Form(""),
        tone: str = Form("professional"),
    ):
        """Draft a reply to a message the caller supplies, using the owner's own
        model endpoint. Account ownership is asserted first. Companion scope."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to draft replies.")
        owner = token_owner(request)
        if not owner:
            raise HTTPException(403, "Could not resolve an owner for this token.")
        _assert_account_owned(account_id, owner)
        if not (original_body or "").strip():
            raise HTTPException(400, "No email body to reply to.")
        safe_tone = (tone or "professional").strip()[:40]
        reply = await _companion_llm(
            owner,
            f"You draft {safe_tone} email replies. Output only the reply body — no subject line, no preamble.",
            f"Subject: {subject}\n\nReply to this email:\n\n{original_body[:12000]}",
            1024,
        )
        return {"reply": (reply or "").strip()}


    @router.post("/upload")
    async def companion_upload(request: Request, files: list[UploadFile] = File(...)):
        """Owner-attributed attachment upload for the paired phone. Returns the
        stock {"files": [{id, name, mime, size, hash, uploaded_at, width, height,
        is_duplicate}]} shape; the client passes the returned ids into
        /api/chat_stream's `attachments`."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to upload attachments.")
        if upload_handler is None:
            raise HTTPException(503, "Upload handler unavailable.")
        owner = token_owner(request)
        if not owner:
            # A bearer token whose owner can't be resolved must not write a
            # null-owner ("shared") upload — fail closed rather than mis-attribute.
            raise HTTPException(403, "Could not resolve an owner for this token.")
        if not files:
            raise HTTPException(400, "No files uploaded")

        import logging as _logging
        import time as _time
        from src.upload_handler import count_recent_uploads

        client_ip = request.client.host if request.client else "unknown"
        # Mirror the stock route's concurrency guard: count genuine recent upload
        # events, not the number of files in this batch (issue #1346).
        recent_uploads = count_recent_uploads(
            upload_handler.upload_rate_log.get(client_ip, []), _time.time()
        )
        if recent_uploads >= upload_handler.max_concurrent_uploads:
            raise HTTPException(
                429,
                f"Maximum concurrent uploads ({upload_handler.max_concurrent_uploads}) exceeded",
            )

        out = []
        for u in files:
            try:
                meta = upload_handler.save_upload(u, client_ip, owner=owner)
                out.append({
                    "id": meta["id"],
                    "name": meta["name"],
                    "mime": meta["mime"],
                    "size": meta["size"],
                    "hash": meta["hash"],
                    "uploaded_at": meta["uploaded_at"],
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "is_duplicate": meta.get("is_duplicate", False),
                })
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 - mirror stock: skip a bad file, keep the rest
                _logging.getLogger(__name__).error(
                    "companion upload failed for %s: %s", getattr(u, "filename", "?"), e
                )
                continue

        if not out:
            raise HTTPException(500, "All file uploads failed")
        return {"files": out}


    @router.get("/upload/{file_id}")
    def companion_upload_file(request: Request, file_id: str, thumb: int = 0):
        """Serve an attachment the caller owns. ?thumb=1 returns a cached <=320px
        JPEG for previews. 404 (never 403) for a missing OR cross-owner file, so a
        non-owner can't confirm a file's existence. Mirrors the stock GET
        /api/upload/{id} serve but scopes ownership to the REAL token owner
        (token_owner) rather than get_current_user, so the phone's own uploads
        (owned by the real user, not "api") are reachable on history reload."""
        if not has_companion_scope(request):
            raise HTTPException(403, "This token is not allowed to read attachments.")
        if upload_handler is None or not upload_handler.validate_upload_id(file_id):
            raise HTTPException(400, "Invalid file ID")

        import json as _json
        import mimetypes as _mt
        import os

        from fastapi.responses import FileResponse
        from src.constants import UPLOAD_DIR

        # Locate the file by matching the requested id against the directory
        # listing, and build the path from the NAME THE LISTING GAVE US. The
        # caller's string is only ever compared, so nothing caller-controlled
        # reaches a filesystem call — a stronger guarantee than joining the raw
        # id and validating afterwards, which depends on every future edit
        # keeping the check in the right order.
        path = None
        for root, _dirs, names in os.walk(UPLOAD_DIR):
            for name in names:
                if name == file_id:
                    path = os.path.join(root, name)
                    break
            if path is not None:
                break
        if path is None or not upload_handler.inside_base_dir(path):
            raise HTTPException(404, "File not found")
        stored_name = os.path.basename(path)

        # Owner check from uploads.json, scoped to the REAL token owner. A file
        # with no metadata entry has an UNPROVABLE owner — deny it (404) rather
        # than treating a missing record as a shared/null-owner row, which would
        # make an orphaned file world-readable to any paired caller. This matches
        # the stock route's fail-closed posture for an unknown owner.
        owner = token_owner(request)
        info = None
        uploads_db = os.path.join(UPLOAD_DIR, "uploads.json")
        if os.path.exists(uploads_db):
            try:
                with open(uploads_db, encoding="utf-8") as f:
                    db = _json.load(f)
                info = next((fi for fi in db.values() if fi.get("id") == file_id), None)
            except Exception:  # noqa: BLE001 - a corrupt index must not leak files
                info = None
        if info is None or not owner_can_see(info.get("owner"), owner):
            raise HTTPException(404, "File not found")

        original_name = (info or {}).get("name", file_id)
        mime = _mt.guess_type(path)[0] or "application/octet-stream"
        if thumb and mime.startswith("image/"):
            try:
                from PIL import Image, ImageOps

                thumb_dir = os.path.join(UPLOAD_DIR, ".thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_path = os.path.join(thumb_dir, stored_name + ".jpg")
                if (not os.path.exists(thumb_path)
                        or os.path.getmtime(thumb_path) < os.path.getmtime(path)):
                    im = Image.open(path)
                    # iPhone/camera JPEGs encode rotation in EXIF; bake it into the
                    # pixels before thumbnailing or the preview comes out sideways.
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((320, 320))
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    im.save(thumb_path, "JPEG", quality=80)
                return FileResponse(thumb_path, media_type="image/jpeg")
            except Exception:  # noqa: BLE001 - fall through to the full image
                pass
        return FileResponse(path, media_type=mime, filename=original_name)

    return router
