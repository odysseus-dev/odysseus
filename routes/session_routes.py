# routes/session_routes.py
import re
import html
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, Form, HTTPException, Response, Request
import logging

from core.session_manager import SessionManager
from core.models import ChatMessage
from src.request_models import SessionResponse
from core.database import Session as DbSession, SessionLocal, Document, GalleryImage
from src.auth_helpers import get_current_user, effective_user


def _sanitize_export_filename(name: str) -> str:
    """Return a conservative filename safe for Content-Disposition."""
    name = name if isinstance(name, str) else ""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:128]


def _verify_session_owner(request: Request, session_id: str, session_manager=None):
    """Verify the current user owns the session. Raises 404 if not.

    Ownership is checked against the DB row when one exists (unchanged). If
    there is no DB row but the caller owns an in-memory "ghost" session — one
    that lives only in ``session_manager`` because it was never persisted, or
    its DB row was removed out-of-band — fall back to the in-memory owner so the
    user can still manage and delete it. Without this fallback such sessions are
    listed by ``/api/sessions`` (they come from the in-memory manager) yet every
    per-session operation 404s, making them impossible to delete (issue #1044).

    ``session_manager`` is optional and defaults to ``None`` so existing callers
    that only care about persisted sessions keep their exact prior behavior.
    """
    user = effective_user(request)
    if not user:
        raise HTTPException(403, "Authentication required")
    db = SessionLocal()
    try:
        row = db.query(DbSession.owner).filter(DbSession.id == session_id).first()
    finally:
        db.close()
    if row is not None:
        if row.owner != user:
            raise HTTPException(404, f"Session {session_id} not found")
        return
    # No DB row — allow the caller to act on an in-memory ghost they own.
    if session_manager is not None:
        ghost = getattr(session_manager, "sessions", {}).get(session_id)
        if ghost is not None and getattr(ghost, "owner", None) == user:
            return
    raise HTTPException(404, f"Session {session_id} not found")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sessions"])


def _current_user_is_admin(request: Request, user: str | None) -> bool:
    if not user:
        return False
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    is_admin = getattr(auth_mgr, "is_admin", None)
    if not callable(is_admin):
        return False
    try:
        return bool(is_admin(user))
    except Exception:
        return False


def _reject_raw_endpoint_url_for_non_admin(
    request: Request,
    user: str | None,
    endpoint_id: str | None,
    endpoint_url: str | None,
) -> None:
    """Require registered endpoints for signed-in non-admin session changes."""
    if endpoint_id and endpoint_id.strip():
        return
    if not endpoint_url:
        return
    # Raw URLs make the server dial whatever host the request supplies. For
    # non-admin users, require a saved endpoint row so normal owner scoping and
    # endpoint validation have already happened.
    if user and not _current_user_is_admin(request, user):
        raise HTTPException(403, "Choose a registered model endpoint")


def _persist_session_headers(session_id: str, headers: dict | None) -> None:
    """Persist endpoint auth headers for DB-backed session metadata."""
    db = SessionLocal()
    try:
        db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
        if db_session:
            db_session.headers = headers or {}
            db_session.updated_at = datetime.utcnow()
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _pick_endpoint_for_sort(owner=None):
    """Pick model endpoint for auto-sort LLM call — uses utility endpoint setting, falls back to default."""
    from src.endpoint_resolver import resolve_endpoint
    # Try utility endpoint first (what the user configured for background tasks)
    url, model, headers = resolve_endpoint("utility", owner=owner)
    if url and model:
        return url, model, headers
    # Fall back to task endpoint
    try:
        from src.task_endpoint import resolve_task_endpoint
        url, model, headers = resolve_task_endpoint(owner=owner)
        if url and model:
            return url, model, headers
    except Exception:
        pass
    # Fall back to default
    url, model, headers = resolve_endpoint("default", owner=owner)
    if url and model:
        return url, model, headers
    return None, None, None

def setup_session_routes(session_manager: SessionManager, config: dict, webhook_manager=None):
    """Setup session routes with the provided manager and config"""

    REQUEST_TIMEOUT = config.get("REQUEST_TIMEOUT", 20)
    OPENAI_API_KEY = config.get("OPENAI_API_KEY")
    SESSIONS_FILE = config.get("SESSIONS_FILE")
    
    @router.get("/sessions")
    def list_sessions(request: Request):
        user = effective_user(request)
        # Lazy purge: incognito sessions are ephemeral by design — wipe leftovers
        # from the DB and session_manager so they vanish on the next page refresh.
        # BUT: skip sessions that were created within the last 10 minutes.
        # Without that guard, the purge nukes the active "Nobody" session on the
        # very first /api/sessions call after creation, killing the in-flight
        # chat. The frontend's own _cleanupIncognitoSessions handler knows which
        # session is current and won't delete the live one — this server-side
        # purge exists only to catch ghosts the frontend missed (tab close,
        # crash). Only clean up rows old enough to be definitely orphaned.
        try:
            from datetime import datetime as _dt, timedelta as _td
            _cutoff = _dt.utcnow() - _td(minutes=10)
            _purge_db = SessionLocal()
            try:
                from core.database import ChatMessage as _DbMsg
                _ghosts = _purge_db.query(DbSession).filter(
                    DbSession.name.in_(("Nobody", "Incognito")),
                    DbSession.created_at < _cutoff,
                ).all()
                for _g in _ghosts:
                    _purge_db.query(_DbMsg).filter(_DbMsg.session_id == _g.id).delete()
                    _purge_db.delete(_g)
                    if hasattr(session_manager, "delete_session"):
                        try:
                            session_manager.delete_session(_g.id)
                        except Exception:
                            pass
                if _ghosts:
                    _purge_db.commit()
            finally:
                _purge_db.close()
        except Exception:
            pass
        user_sessions = session_manager.get_sessions_for_user(user)
        # Fetch folder info from DB for each session
        db = SessionLocal()
        try:
            folder_map = {}
            token_map = {}
            important_map = {}
            created_map = {}
            updated_map = {}
            last_msg_map = {}
            mode_map = {}
            msg_count_map = {}
            rows = db.query(DbSession.id, DbSession.folder, DbSession.total_input_tokens, DbSession.total_output_tokens, DbSession.is_important, DbSession.created_at, DbSession.updated_at, DbSession.last_message_at, DbSession.mode, DbSession.message_count).filter(DbSession.archived == False).all()
            for row in rows:
                folder_map[row.id] = row.folder
                token_map[row.id] = (row.total_input_tokens or 0) + (row.total_output_tokens or 0)
                important_map[row.id] = row.is_important or False
                created_map[row.id] = row.created_at.isoformat() if row.created_at else None
                updated_map[row.id] = row.updated_at.isoformat() if row.updated_at else None
                # Fall back to updated_at then created_at so sessions that
                # predate the column (or have no messages) still sort sanely.
                last_msg_map[row.id] = (
                    row.last_message_at.isoformat() if row.last_message_at
                    else (row.updated_at.isoformat() if row.updated_at
                          else (row.created_at.isoformat() if row.created_at else None))
                )
                mode_map[row.id] = row.mode
                msg_count_map[row.id] = row.message_count or 0
            # Sessions with active documents that have content
            from sqlalchemy import func
            doc_session_ids = set(
                r[0] for r in db.query(Document.session_id)
                .filter(Document.is_active == True,
                        Document.current_content != None,
                        func.trim(Document.current_content) != "")
                .distinct().all()
            )
            img_session_ids = set(
                r[0] for r in db.query(GalleryImage.session_id)
                .filter(GalleryImage.session_id != None)
                .distinct().all()
            )
        finally:
            db.close()

        sessions = [{"id": s.id, "name": s.name, "model": s.model,
                     "endpoint_url": s.endpoint_url, "rag": s.rag,
                     "archived": s.archived, "folder": folder_map.get(s.id),
                     "total_tokens": token_map.get(s.id, 0),
                     "is_important": important_map.get(s.id, False),
                     "created_at": created_map.get(s.id),
                     "updated_at": updated_map.get(s.id),
                     "last_message_at": last_msg_map.get(s.id),
                     "has_documents": s.id in doc_session_ids,
                     "has_images": s.id in img_session_ids,
                     "mode": mode_map.get(s.id),
                     "message_count": msg_count_map.get(s.id, 0)}
                    for s in user_sessions.values()
                    if not s.archived
                    and (s.name or "").strip() not in ("Nobody", "Incognito")]

        return sessions
    
    @router.post("/session", response_model=SessionResponse)
    def create_session(
        request: Request,
        name: str = Form(""),
        endpoint_url: str = Form(""),
        model: str = Form(""),
        rag: str = Form(None),
        skip_validation: str = Form(None),
        api_key: str = Form(""),
        endpoint_id: str = Form(""),
    ):
        skip_val = str(skip_validation).lower() == "true"
        user = get_current_user(request)
        endpoint_api_key = ""
        endpoint_base_url = ""
        _reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url)
        if endpoint_id and endpoint_id.strip():
            from core.database import ModelEndpoint
            from src.auth_helpers import owner_filter
            from src.endpoint_resolver import build_chat_url, normalize_base
            _db = SessionLocal()
            try:
                q = _db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == endpoint_id.strip(),
                    ModelEndpoint.is_enabled == True,
                )
                if user:
                    q = owner_filter(q, ModelEndpoint, user)
                endpoint_row = q.first()
                if not endpoint_row:
                    raise HTTPException(400, "Model endpoint no longer exists")
                endpoint_base_url = endpoint_row.base_url or ""
                endpoint_api_key = endpoint_row.api_key or ""
                endpoint_url = build_chat_url(normalize_base(endpoint_base_url))
            finally:
                _db.close()

        if not endpoint_url and not skip_val:
            raise HTTPException(400, "endpoint_url is required (choose from /api/models)")

        model_to_use = model
        request_api_key = api_key.strip() if api_key else ""
        effective_api_key = request_api_key or endpoint_api_key
        validation_headers = None
        if effective_api_key:
            from src.endpoint_resolver import build_headers
            validation_headers = build_headers(effective_api_key, endpoint_base_url or endpoint_url)

        if skip_val:
            # skip_validation = trust the caller and do NOT probe /v1/models.
            # Used for custom endpoints AND for bare placeholder sessions with no
            # model at all (e.g. an email reply draft just needs a session to live
            # in). Probing here was 400-ing those with "Cannot reach /v1/models".
            pass
        elif not model_to_use:
            from src.llm_core import list_model_ids
            ids = list_model_ids(endpoint_url, timeout=REQUEST_TIMEOUT,
                                 headers=validation_headers)
            if not ids:
                raise HTTPException(400, "Cannot reach /v1/models")
            # Default to the first CHAT model — endpoints often list embedding/
            # tts/whisper models first (e.g. text-embedding-ada-002), which
            # can't hold a conversation.
            _NON_CHAT = ("text-embedding", "embedding", "tts-", "whisper",
                         "text-moderation", "moderation-", "dall-e", "rerank")
            chat_ids = [m for m in ids if not any(p in m.lower() for p in _NON_CHAT)]
            model_to_use = (chat_ids or ids)[0]
        else:
            from src.llm_core import list_model_ids
            import os as _os
            req_base = _os.path.basename(model_to_use.rstrip("/"))
            avail = list_model_ids(endpoint_url, timeout=REQUEST_TIMEOUT,
                                   headers=validation_headers)
            if not avail:
                raise HTTPException(400, "Cannot reach /v1/models")
            if model_to_use not in avail:
                found = None
                for a in avail:
                    if _os.path.basename(a.rstrip("/")) == req_base:
                        found = a
                        break
                if not found:
                    raise HTTPException(400,
                                        f"Model not found at server. Available: {', '.join(avail)}")
                model_to_use = found
        
        sid = str(uuid.uuid4())
        user = effective_user(request)
        session = session_manager.create_session(
            session_id=sid,
            name=name or "",
            endpoint_url=endpoint_url or "",
            model=model_to_use,
            rag=str(rag).lower() == "true" if rag else False,
            owner=user,
        )
        # Set auth headers for custom API-key endpoints
        resolved_key = request_api_key
        resolved_base = endpoint_url
        if not resolved_key and endpoint_api_key:
            resolved_key = endpoint_api_key
            resolved_base = endpoint_base_url
        if resolved_key:
            from src.endpoint_resolver import build_headers
            session.headers = build_headers(resolved_key, resolved_base)
            _persist_session_headers(sid, session.headers)
        # Fire webhook (sync-safe)
        if webhook_manager:
            webhook_manager.fire_and_forget("session.created", {
                "session_id": sid, "name": session.name, "model": model_to_use,
            })
        # Fire event for automation tasks
        from src.event_bus import fire_event
        fire_event("session_created", user)
        return SessionResponse(
            id=sid,
            name=session.name,
            model=model_to_use,
            rag=str(rag).lower() == "true" if rag else False,
            archived=False
        )    
    @router.patch("/session/{sid}")
    def rename_session(
        request: Request, sid: str,
        name: str = Form(None), folder: str = Form(None),
        model: str = Form(None), endpoint_url: str = Form(None),
        endpoint_id: str = Form(None),
    ):
        _verify_session_owner(request, sid)
        try:
            session = session_manager.get_session(sid)
        except KeyError:
            raise HTTPException(404, f"Session {sid} not found")
        result = {"id": sid}
        if name is not None:
            session_manager.update_session_name(sid, name)
            result["name"] = name
        # Update folder assignment
        if folder is not None:
            db = SessionLocal()
            try:
                db_session = db.query(DbSession).filter(DbSession.id == sid).first()
                if db_session:
                    db_session.folder = folder if folder else None
                    db_session.updated_at = datetime.utcnow()
                    db.commit()
                    result["folder"] = folder if folder else None
            finally:
                db.close()
        # Switch model/endpoint mid-session
        if model is not None and endpoint_url is not None:
            user = get_current_user(request)
            _reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url)
            endpoint_api_key = ""
            endpoint_base_url = ""
            if endpoint_id:
                from core.database import ModelEndpoint
                from src.auth_helpers import owner_filter
                from src.endpoint_resolver import build_chat_url, normalize_base
                _db = SessionLocal()
                try:
                    q = _db.query(ModelEndpoint).filter(
                        ModelEndpoint.id == endpoint_id,
                        ModelEndpoint.is_enabled == True,
                    )
                    if user:
                        q = owner_filter(q, ModelEndpoint, user)
                    ep = q.first()
                    if not ep:
                        raise HTTPException(400, "Model endpoint no longer exists")
                    endpoint_base_url = ep.base_url or ""
                    endpoint_api_key = ep.api_key or ""
                    endpoint_url = build_chat_url(normalize_base(endpoint_base_url))
                finally:
                    _db.close()
            session.model = model
            session.endpoint_url = endpoint_url
            # Update auth headers from the endpoint's stored API key
            if endpoint_api_key:
                from src.endpoint_resolver import build_headers
                session.headers = build_headers(endpoint_api_key, endpoint_base_url)
            else:
                session.headers = {}
            # Persist to DB
            db = SessionLocal()
            try:
                db_session = db.query(DbSession).filter(DbSession.id == sid).first()
                if db_session:
                    db_session.model = model
                    db_session.endpoint_url = endpoint_url
                    db_session.headers = session.headers or {}
                    db_session.updated_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()
            result["model"] = model
            result["endpoint_url"] = endpoint_url
        return result
    
    @router.post("/session/{sid}/inject_messages")
    async def inject_messages(request: Request, sid: str):
        """Bulk-inject messages into a session's history (for group chat sync)."""
        _verify_session_owner(request, sid)
        try:
            sess = session_manager.get_session(sid)
        except KeyError:
            raise HTTPException(404, f"Session {sid} not found")
        body = await request.json()
        messages = body.get("messages", [])
        from core.models import ChatMessage
        for m in messages:
            sess.add_message(ChatMessage(m["role"], m["content"], metadata=m.get("metadata")))
        session_manager.save_sessions()
        return {"ok": True, "count": len(messages)}

    @router.post("/session/{sid}/delete")
    def delete_session_beacon(request: Request, sid: str):
        """Delete session via POST (for navigator.sendBeacon on page close)."""
        return delete_session(request, sid)

    @router.post("/sessions/bulk-delete")
    async def bulk_delete_sessions(request: Request):
        """Delete multiple sessions (for compare cleanup via sendBeacon)."""
        from core.database import ChatMessage as _CM
        try:
            body = await request.json()
            ids = body.get("ids", [])
        except Exception:
            ids = []
        for sid in ids:
            try:
                _verify_session_owner(request, sid, session_manager)
                session_manager.delete_session(sid)
                db = SessionLocal()
                try:
                    db.query(_CM).filter(_CM.session_id == sid).delete()
                    db.query(DbSession).filter(DbSession.id == sid).delete()
                    db.commit()
                except Exception:
                    db.rollback()
                finally:
                    db.close()
            except Exception:
                pass
        return {"deleted": len(ids)}

    @router.delete("/session/{sid}")
    def delete_session(request: Request, sid: str):
        """Permanently delete a session and all its messages."""
        _verify_session_owner(request, sid, session_manager)
        try:
            # Block deletion of starred/favorited sessions
            db = SessionLocal()
            try:
                db_sess = db.query(DbSession).filter(DbSession.id == sid).first()
                if db_sess and db_sess.is_important:
                    raise HTTPException(
                        status_code=403,
                        detail={"error": "SESSION_STARRED", "message": "Unstar the session before deleting it"}
                    )
            finally:
                db.close()

            # Delete the session and all its messages
            if session_manager.delete_session(sid):
                return {"status": "deleted"}
            else:
                raise HTTPException(404, "Session not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting session {sid}: {e}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "SESSION_DELETE_ERROR",
                    "message": "Failed to delete session"
                }
            )
    
    @router.delete("/sessions/all")
    def delete_all_sessions(request: Request):
        """Admin only: permanently delete ALL sessions and their messages."""
        from core.middleware import require_admin
        require_admin(request)

        db = SessionLocal()
        try:
            from core.database import ChatMessage as DbChatMessage
            count = db.query(DbSession).count()
            db.query(DbChatMessage).delete()
            db.query(DbSession).delete()
            db.commit()
            session_manager.sessions.clear()
            logger.info(f"Admin deleted all {count} sessions")
            return {"status": "deleted", "count": count}
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting all sessions: {e}")
            raise HTTPException(500, "Failed to delete sessions")
        finally:
            db.close()

    @router.post("/session/{sid}/archive")
    def archive_session(request: Request, sid: str):
        """Archive a session, keeping its data but removing it from active sessions."""
        _verify_session_owner(request, sid)
        try:
            # First check if session exists
            session_manager.get_session(sid)
            
            # Archive the session
            db = SessionLocal()
            try:
                db_session = db.query(DbSession).filter(DbSession.id == sid).first()
                if db_session:
                    db_session.archived = True
                    db_session.updated_at = datetime.utcnow()
                    db.commit()
                    
                    # Update in memory if it exists
                    if sid in session_manager.sessions:
                        session_manager.sessions[sid].archived = True
                        
                    logger.info(f"Archived session {sid}")
                    return {"status": "archived"}
                else:
                    raise HTTPException(404, f"Session {sid} not found")
                    
            except HTTPException:
                raise
            except Exception as e:
                db.rollback()
                logger.error(f"Error archiving session {sid}: {e}")
                raise HTTPException(500, "Failed to archive session")
            finally:
                db.close()

        except KeyError:
            raise HTTPException(404, f"Session '{sid}' not found")
    
    @router.post("/session/{sid}/unarchive")
    def unarchive_session(request: Request, sid: str):
        """Restore an archived session back to the active session list."""
        _verify_session_owner(request, sid)
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == sid).first()
            if not db_session:
                raise HTTPException(404, f"Session {sid} not found")
            db_session.archived = False
            db_session.updated_at = datetime.utcnow()
            db.commit()
            # Reload into session manager so it appears in the active list
            try:
                if sid in session_manager.sessions:
                    session_manager.sessions[sid].archived = False
                else:
                    session_manager._load_session_from_db(sid)
            except Exception:
                pass  # Non-fatal — session will load on next access
            return {"status": "unarchived"}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Error unarchiving session {sid}: {e}")
            raise HTTPException(500, "Failed to unarchive session")
        finally:
            db.close()

    @router.get("/sessions/archived")
    def list_archived_sessions(request: Request, search: str = "", offset: int = 0, limit: int = 20, sort: str = "recent", model: str = ""):
        """List archived sessions for the archive browser."""
        user = effective_user(request)
        db = SessionLocal()
        try:
            q = db.query(DbSession).filter(DbSession.archived == True)
            if not user:
                raise HTTPException(403, "Authentication required")
            q = q.filter(DbSession.owner == user)
            if search:
                safe_search = search.replace('%', r'\%').replace('_', r'\_')
                q = q.filter(DbSession.name.ilike(f"%{safe_search}%", escape='\\'))
            if model:
                # Contains match (mirrors the name filter above). The old
                # f"%{model}" was a SUFFIX-only match, so filtering by "gpt-4"
                # dropped "gpt-4o" and over-matched on shared suffixes; it also
                # left LIKE wildcards in the user value unescaped.
                safe_model = model.replace('%', r'\%').replace('_', r'\_')
                q = q.filter(DbSession.model.ilike(f"%{safe_model}%", escape='\\'))
            total = q.count()
            sort_map = {
                "recent": DbSession.updated_at.desc(),
                "oldest": DbSession.updated_at.asc(),
                "most-messages": DbSession.message_count.desc().nulls_last(),
                "alpha": DbSession.name.asc(),
            }
            order = sort_map.get(sort, DbSession.updated_at.desc())
            rows = q.order_by(order).offset(offset).limit(limit).all()
            sessions = []
            for s in rows:
                sessions.append({
                    "id": s.id,
                    "name": s.name,
                    "model": s.model,
                    "message_count": s.message_count or 0,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "is_important": s.is_important,
                })
            return {"sessions": sessions, "total": total}
        finally:
            db.close()

    @router.get("/history/{sid}")
    def get_history(request: Request, sid: str):
        _verify_session_owner(request, sid)
        try:
            session = session_manager.get_session(sid)
        except KeyError:
            raise HTTPException(404, f"Session {sid} not found")
        return {"history": [msg.to_dict() for msg in session.history]}
    
    @router.get("/session/{sid}/export")
    def export_session(request: Request, sid: str, fmt: str = "md", filename: str = ""):
        """Export conversation history as a downloadable file.

        Supported formats: md (markdown), txt (plain text), json, html
        """
        _verify_session_owner(request, sid)
        try:
            session = session_manager.get_session(sid)
        except KeyError:
            raise HTTPException(404, f"Session {sid} not found")

        safe_name = re.sub(r'[^\w\-_]', '_', session.name)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = _sanitize_export_filename(filename)

        if fmt == "json":
            import json as _json
            data = {
                "name": session.name,
                "model": session.model,
                "exported": datetime.now().isoformat(),
                "messages": [{"role": m.role, "content": m.content} for m in session.history],
            }
            out_name = filename or f"conversation_{safe_name}_{timestamp}.json"
            return Response(
                content=_json.dumps(data, indent=2, ensure_ascii=False),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename={out_name}"},
            )

        if fmt == "txt":
            lines = []
            for m in session.history:
                lines.append(f"[{m.role.upper()}]")
                lines.append(m.content)
                lines.append("")
            out_name = filename or f"conversation_{safe_name}_{timestamp}.txt"
            return Response(
                content="\n".join(lines),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={out_name}"},
            )

        if fmt == "html":
            safe_title = html.escape(session.name or "")
            html_parts = [
                "<!DOCTYPE html><html><head>",
                f"<meta charset='utf-8'><title>{safe_title}</title>",
                "<style>body{font-family:monospace;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#ddd}",
                ".msg{margin:1rem 0;padding:0.8rem;border-radius:6px;border:1px solid #333}",
                ".user{background:#1a1a2e}.ai{background:#1a2e1a}",
                ".role{font-weight:bold;margin-bottom:0.4rem;opacity:0.7;text-transform:uppercase;font-size:0.85em}",
                "pre{background:#000;padding:0.5rem;border-radius:4px;overflow-x:auto}</style></head><body>",
                f"<h1>{safe_title}</h1>",
            ]
            for m in session.history:
                cls = "user" if m.role == "user" else "ai"
                content = m.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                content = content.replace("\n", "<br>")
                html_parts.append(f'<div class="msg {cls}"><div class="role">{m.role}</div>{content}</div>')
            html_parts.append("</body></html>")
            out_name = filename or f"conversation_{safe_name}_{timestamp}.html"
            return Response(
                content="\n".join(html_parts),
                media_type="text/html",
                headers={"Content-Disposition": f"attachment; filename={out_name}"},
            )

        # Default: markdown
        markdown_lines = []
        markdown_lines.append(f"# Conversation: {session.name}")
        markdown_lines.append(f"*Exported on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        markdown_lines.append(f"*Model: {session.model}*")
        markdown_lines.append("\n---\n")
        for message in session.history:
            role = message.role.upper()
            content = message.content
            markdown_lines.append(f"### {role}")
            markdown_lines.append(f"{content}\n")
            markdown_lines.append("---\n")
        if len(markdown_lines) > 3:
            markdown_lines.pop()
        out_name = filename or f"conversation_{safe_name}_{timestamp}.md"
        return Response(
            content="\n".join(markdown_lines),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={out_name}"},
        )
    
    @router.post("/sessions/save")
    def sessions_save_now(request: Request):
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        session_manager.save_sessions()
        return {"ok": True, "path": SESSIONS_FILE}
    
    @router.post("/session/openai")
    def create_session_openai(
        request: Request,
        name: str = Form("New Chat (OpenAI)"),
        model: str = Form("gpt-4o"),
        rag: str = Form(None)
    ):
        if not OPENAI_API_KEY:
            raise HTTPException(400, "Server missing OPENAI_API_KEY")
        sid = str(uuid.uuid4())
        user = effective_user(request)
        session = session_manager.create_session(
            session_id=sid,
            name="",
            endpoint_url="https://api.openai.com/v1/chat/completions",
            model=model,
            rag=str(rag).lower() == "true",
            owner=user,
        )
        session.headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        session_manager.save_sessions()
        from src.event_bus import fire_event
        fire_event("session_created", user)
        return {"id": sid, "name": "", "model": model}
    
    @router.post("/session/{session_id}/important")
    async def mark_session_important(request: Request, session_id: str, important: bool = Form(True)):
        """Mark a session as important to protect it from automatic cleanup."""
        _verify_session_owner(request, session_id)
        try:
            # Validate session exists
            session_manager.get_session(session_id)

            # Update in database
            db = SessionLocal()
            try:
                db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
                if db_session:
                    db_session.is_important = important
                    db_session.updated_at = datetime.utcnow()
                    db.commit()

                    # Update in memory if it exists
                    if session_id in session_manager.sessions:
                        session_manager.sessions[session_id].is_important = important

                    return {"status": "success", "is_important": important}
                else:
                    raise HTTPException(404, f"Session {session_id} not found")

            except HTTPException:
                raise
            except Exception as e:
                db.rollback()
                logger.error(f"Error updating session {session_id} importance: {e}")
                raise HTTPException(500, "Failed to update session importance")
            finally:
                db.close()

        except KeyError:
            raise HTTPException(404, f"Session {session_id} not found")

    @router.post("/session/{session_id}/compact")
    async def compact_session(request: Request, session_id: str):
        """Summarize older messages into one compacted history entry."""
        _verify_session_owner(request, session_id)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, f"Session {session_id} not found")

        history = list(session.history or [])
        if len(history) < 6:
            raise HTTPException(400, "Not enough messages to compact")

        # Keep a small recent tail verbatim. The prior half-chat/20-message
        # tail made manual compaction look like it did nothing on normal chats.
        recent_keep = min(8, max(4, len(history) // 4))
        older = history[:-recent_keep]
        recent = history[-recent_keep:]
        if not older:
            raise HTTPException(400, "Nothing old enough to compact")

        from src.context_compactor import SELF_SUMMARY_SYSTEM_PROMPT
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async

        url, model, headers = resolve_endpoint("utility", owner=get_current_user(request))
        if not url or not model:
            url, model, headers = session.endpoint_url, session.model, session.headers
        if not url or not model:
            raise HTTPException(400, "No model configured for compaction")

        prior_compactions = sum(
            1 for m in history
            if (m.metadata or {}).get("compacted") or "[Conversation summary" in (m.content or "")
        )
        prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace(
            "{count}", str(len(older))
        ).replace(
            "{n}", str(prior_compactions + 1)
        )
        convo_text = "\n".join(
            f"{m.role.upper()}: {(m.content or '')[:2000]}"
            for m in older
        )
        try:
            summary = await llm_call_async(
                url,
                model,
                [{"role": "system", "content": prompt}, {"role": "user", "content": convo_text}],
                temperature=0.2,
                max_tokens=1024,
                headers=headers,
                timeout=60,
            )
        except Exception as e:
            logger.error("Manual compaction failed: %s", e)
            raise HTTPException(500, "Compaction failed")

        summary_msg = ChatMessage(
            role="system",
            content=f"[Conversation summary]\n{summary}",
            metadata={
                "compacted": True,
                "summarized_count": len(older),
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        new_history = [summary_msg] + recent
        if not session_manager.replace_messages(session_id, new_history):
            raise HTTPException(500, "Failed to save compacted history")

        return {
            "ok": True,
            "summarized": len(older),
            "kept": len(recent),
            "message_count": len(new_history),
        }

    @router.post("/sessions/auto-sort")
    def auto_sort_sessions(request: Request, skip_llm: bool = False):
        """Use AI to categorize all sessions into folders.

        Phase 1 deletes empty/throwaway sessions and Phase 2 asks the LLM
        to assign folders. When `skip_llm=true` the endpoint returns
        after Phase 1 — used by the "Tidy (no AI)" UI affordance so
        users can clean junk without spending tokens.
        """
        from src.llm_core import llm_call
        user = effective_user(request)
        user_sessions = session_manager.get_sessions_for_user(user)

        # Delete empty and throwaway sessions before sorting
        from core.database import ChatMessage as DbMsg
        db = SessionLocal()
        deleted_empty = 0
        deleted_throwaway = 0
        # Names that indicate a throwaway/test session (case-insensitive exact or prefix match)
        _THROWAWAY_NAMES = {
            "test", "testing", "asdf", "asd", "hello", "hi", "hey",
            "yo", "sup", "hola", "hii", "hiii", "heyo",
            "foo", "bar", "baz", "tmp", "temp", "scratch", "untitled",
            "new chat", "delete", "remove", "junk", "trash", "xxx",
            "abc", "qwerty", "blah", "stuff", "whatever", "idk",
            "ok", "lol", "bruh", "hmm", "hm", "meh",
        }
        _THROWAWAY_MAX_MESSAGES = 4  # only delete if <= this many messages
        try:
            rows = db.query(DbSession).filter(DbSession.archived == False, DbSession.owner == user).all()
            folder_map = {r.id: r.folder for r in rows}
            # Precompute per-session message counts in TWO aggregate queries
            # instead of 1–3 queries PER session — with many chats the per-row
            # loop was doing thousands of round-trips and blowing the timeout.
            from sqlalchemy import func as _sa_func
            _counts = dict(db.query(DbMsg.session_id, _sa_func.count(DbMsg.id)).group_by(DbMsg.session_id).all())
            _asst_counts = dict(
                db.query(DbMsg.session_id, _sa_func.count(DbMsg.id))
                .filter(DbMsg.role == "assistant").group_by(DbMsg.session_id).all()
            )
            for row in rows:
                # Never delete important sessions
                if getattr(row, 'is_important', False):
                    continue
                # Always delete incognito sessions during cleanup
                if (row.name or "").strip() == "Incognito":
                    should_delete = True
                    deleted_throwaway += 1
                    db.delete(row)
                    if hasattr(session_manager, 'delete_session'):
                        session_manager.delete_session(row.id)
                    continue
                msg_count = _counts.get(row.id, 0)
                should_delete = False
                if msg_count == 0:
                    should_delete = True
                    deleted_empty += 1
                elif msg_count <= _THROWAWAY_MAX_MESSAGES:
                    name = (row.name or "").strip().lower()
                    # Check first user message content (AI renames sessions, so
                    # "hi" becomes "Casual Greeting Exchange" — name alone won't match)
                    first_msg = db.query(DbMsg.content).filter(
                        DbMsg.session_id == row.id, DbMsg.role == "user"
                    ).order_by(DbMsg.timestamp).first()
                    first_text = (first_msg[0] or "").strip().lower() if first_msg else ""
                    # Count assistant messages — if user sent something but AI never replied, it's dead
                    assistant_count = _asst_counts.get(row.id, 0)
                    if name in _THROWAWAY_NAMES or name.startswith("chat:") or first_text in _THROWAWAY_NAMES:
                        should_delete = True
                        deleted_throwaway += 1
                    # Single user message with no AI response = dead session
                    elif msg_count == 1 and assistant_count == 0:
                        should_delete = True
                        deleted_throwaway += 1
                    # Short phrase (1-3 words) with no real AI conversation (<=2 msgs)
                    elif msg_count <= 2 and first_text and len(first_text.split()) <= 3 and len(first_text) <= 40:
                        should_delete = True
                        deleted_throwaway += 1
                if should_delete:
                    db.delete(row)
                    if hasattr(session_manager, 'delete_session'):
                        session_manager.delete_session(row.id)
            if deleted_empty or deleted_throwaway:
                db.commit()
                logger.info(f"Auto-sort: deleted {deleted_empty} empty + {deleted_throwaway} throwaway sessions")
        finally:
            db.close()

        # Re-fetch after cleanup
        if deleted_empty or deleted_throwaway:
            user_sessions = session_manager.get_sessions_for_user(user)

        # Short-circuit when the caller only wanted the cleanup phase
        # (the "Tidy (no AI)" path). Shape mirrors the post-Phase-1
        # branch below so the frontend can render the same toast.
        if skip_llm:
            return {
                "status": "ok",
                "updated": 0,
                "folders": [],
                "deleted_empty": deleted_empty,
                "deleted_throwaway": deleted_throwaway,
                "unfiled_remaining": 0,
                "skipped_llm": True,
            }

        # Tidy works in batches: only sessions that don't already have a
        # folder, capped at TIDY_BATCH_SIZE (most recent first). Sending
        # all 100+ chats to one LLM call blows the context window, makes
        # the request slow, and re-bills the same tokens every click for
        # already-sorted chats. Skipping sessions with `current_folder`
        # means each Tidy press only handles new unfiled chats.
        TIDY_BATCH_SIZE = 15
        all_candidates = []
        for s in user_sessions.values():
            if s.archived or s.name == "Incognito":
                continue
            if folder_map.get(s.id):
                # Already in a folder — skip on this pass.
                continue
            name = s.name or "(unnamed)"
            all_candidates.append({
                "id": s.id,
                "name": name,
                "updated_at": getattr(s, "updated_at", None) or getattr(s, "created_at", None) or "",
                "current_folder": None,
            })

        # Most-recent first, then take the top N for this batch.
        all_candidates.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        unfiled_total = len(all_candidates)
        session_list = all_candidates[:TIDY_BATCH_SIZE]

        if len(session_list) < 2:
            if deleted_empty or deleted_throwaway:
                return {
                    "status": "ok",
                    "updated": 0,
                    "folders": [],
                    "deleted_empty": deleted_empty,
                    "deleted_throwaway": deleted_throwaway,
                    "unfiled_remaining": unfiled_total,
                }
            return {"status": "skipped", "reason": "No unfiled sessions to sort"}

        # Pick an endpoint — prefer admin-configured task endpoint
        from src.task_endpoint import resolve_task_endpoint
        url, model, headers = resolve_task_endpoint(owner=user)
        if not url:
            url, model, headers = _pick_endpoint_for_sort(owner=user)
        if not url:
            raise HTTPException(503, "No available model endpoint for auto-sort")

        # Build prompt
        names_text = "\n".join(f'  "{s["id"][:8]}": "{s["name"]}"' for s in session_list)
        prompt = (
            "You are a session organizer. Group these chat sessions into folders by topic.\n\n"
            "Rules:\n"
            "- Be aggressive about grouping — put EVERY session in a folder\n"
            "- Use short folder names (2-4 words max)\n"
            "- Use the 8-char ID prefixes exactly as given\n"
            "- Output ONLY raw JSON, no markdown fences, no explanation\n\n"
            "Required JSON format:\n"
            '{"folders": {"Folder Name": ["id_prefix1", "id_prefix2"], "Other Folder": ["id_prefix3"]}}\n\n'
            f"Sessions (id_prefix: name):\n{{\n{names_text}\n}}"
        )

        try:
            logger.info(f"Auto-sort: using model={model} at {url}")
            # 16384 (was 4096): with many chats the folder JSON is large, and a
            # reasoning model spends tokens thinking first — 4096 truncated the
            # JSON mid-output, so it never parsed ("invalid JSON for auto-sort").
            raw = llm_call(url, model, [{"role": "user", "content": prompt}],
                           temperature=0.3, max_tokens=16384, headers=headers, timeout=120)
            logger.info(f"Auto-sort raw response ({len(raw)} chars): {raw[:300]}")
            # Extract JSON from response — handle markdown fences, leading text,
            # reasoning-model <think> blocks, and trailing commas.
            text = raw.strip()
            # Reasoning models emit <think>…</think> (often containing { } that
            # would derail the brace scan) before the answer — drop it first.
            text = re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', text, flags=re.I).strip()

            def _loads_lenient(s):
                """Parse JSON, retrying once with trailing commas stripped."""
                if not s:
                    return None
                for cand in (s, re.sub(r',(\s*[}\]])', r'\1', s)):
                    try:
                        return json.loads(cand)
                    except json.JSONDecodeError:
                        continue
                return None

            result = _loads_lenient(text)
            # Markdown code fence
            if result is None:
                fence_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)
                if fence_match:
                    result = _loads_lenient(fence_match.group(1).strip())
            # First { … last } block
            if result is None:
                brace_start = text.find('{')
                brace_end = text.rfind('}')
                if brace_start >= 0 and brace_end > brace_start:
                    result = _loads_lenient(text[brace_start:brace_end + 1])
            if result is None:
                logger.error(f"Auto-sort: could not parse JSON from: {text[:500]}")
                raise HTTPException(502, "AI returned invalid JSON for auto-sort — the model may not follow JSON instructions; try a different utility model in Settings.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Auto-sort LLM call failed: {e}")
            raise HTTPException(502, f"Auto-sort failed: {str(e)}")

        folders = result.get("folders", {})
        if not folders:
            return {"status": "skipped", "reason": "AI found no groupings"}

        # Build id -> folder map
        id_prefix_map = {s["id"][:8]: s["id"] for s in session_list}
        assignments = {}
        for folder_name, ids in folders.items():
            for sid_or_prefix in ids:
                # Match by full ID or prefix
                full_id = None
                if sid_or_prefix in id_prefix_map.values():
                    full_id = sid_or_prefix
                else:
                    # Try prefix match
                    prefix = sid_or_prefix.rstrip(".").rstrip(" ")
                    if prefix in id_prefix_map:
                        full_id = id_prefix_map[prefix]
                    else:
                        # Fuzzy prefix match
                        for p, fid in id_prefix_map.items():
                            if fid.startswith(prefix) or prefix.startswith(p):
                                full_id = fid
                                break
                if full_id:
                    assignments[full_id] = folder_name

        # Apply folder assignments
        updated = 0
        db = SessionLocal()
        try:
            for sid, folder_name in assignments.items():
                db_session = db.query(DbSession).filter(DbSession.id == sid, DbSession.owner == user).first()
                if db_session:
                    db_session.folder = folder_name
                    db_session.updated_at = datetime.utcnow()
                    updated += 1
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Auto-sort DB update failed: {e}")
            raise HTTPException(500, "Failed to apply folder assignments")
        finally:
            db.close()

        # How many unfiled chats are left after this batch — the
        # frontend uses this to decide whether to show "Tidy more" or
        # "All sorted!" in the toast.
        unfiled_remaining_after = max(0, unfiled_total - updated)
        return {
            "status": "ok",
            "folders": list(folders.keys()),
            "updated": updated,
            "deleted_empty": deleted_empty,
            "deleted_throwaway": deleted_throwaway,
            "unfiled_remaining": unfiled_remaining_after,
        }

    @router.get("/session/{session_id}/context_info")
    async def get_context_info(request: Request, session_id: str):
        """Get the real context length for a session's model from the endpoint."""
        _verify_session_owner(request, session_id)
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        if not session.endpoint_url or not session.model:
            return {"context_length": None}
        try:
            from src.model_context import get_context_length
            ctx = get_context_length(session.endpoint_url, session.model)
            return {"context_length": ctx, "model": session.model}
        except Exception:
            return {"context_length": None}

    return router
