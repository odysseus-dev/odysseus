"""Companion bridge — /api/companion/*.

A thin, additive layer so a LAN client (e.g. a phone) can discover what a server
offers and pair to it, without duplicating any LLM logic.

Auth is enforced globally by AuthMiddleware (app.py), so reaching a handler here
means the caller is authenticated by either a cookie session or a Bearer `ody_`
API token. The read endpoints (ping/info/models) accept either; the pairing
endpoints are admin-cookie only.

Pairing CSRF posture: minting happens ONLY on POST. The session cookie is
SameSite=Lax (routes/auth_routes.py), which a browser does not send on a
cross-site POST, so an admin's cookie can't be used by a malicious page to mint
a token -- the same protection the existing POST /api/tokens relies on. Minting
on a GET would be unsafe (Lax cookies ride top-level GET navigations), so GET
/pair only renders a form.
"""

import html

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from core.middleware import require_admin
from src.auth_helpers import get_current_user

from companion import pairing as _pairing


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


def _require_owner(request: Request) -> str:
    """Resolve the token's real owner or 401. Shared by the tool-proxy endpoints
    (email/calendar/notes/tasks)."""
    owner = token_owner(request)
    if not owner:
        raise HTTPException(401, "Could not resolve an owner for this token")
    return owner


def owner_can_see(row_owner, owner) -> bool:
    """Owner-scope rule for read endpoints.

    A caller sees a row when it is their own, or when it is a legacy null-owner
    ("shared") row. A caller must NEVER see another owner's row. Mirrors the
    `owner_filter` rule used elsewhere, expressed as a pure predicate so it can
    be tested directly and used as a defensive in-Python check alongside the
    SQL filter.
    """
    return row_owner is None or row_owner == owner


_fallback_session_manager = None


def _resolve_session_manager(injected):
    """Use the app-shared SessionManager when wired in (so the live in-memory
    sessions match the owner's desktop UI), falling back to a lazily-built one
    for standalone/test contexts that call setup_companion_routes() with no
    args. Kept module-level so it can be monkeypatched in tests."""
    global _fallback_session_manager
    if injected is not None:
        return injected
    if _fallback_session_manager is None:
        from core.session_manager import SessionManager
        _fallback_session_manager = SessionManager()
    return _fallback_session_manager


def session_summary(sess, active: bool, status, public_model=None) -> dict:
    """Shape one session row for a companion client. Pure so it's unit-testable.

    `active`/`status` come from agent_runs (is_active/get_status) so the phone
    knows which sessions are still generating and can reconnect to the live
    stream without a probe request. `public_model` lets the caller blank a
    blind-compare session's real model (issue #1285); defaults to the stored
    model.
    """
    return {
        "id": sess.id,
        "name": sess.name,
        "model": sess.model if public_model is None else public_model,
        "message_count": getattr(sess, "message_count", 0) or 0,
        "is_important": bool(getattr(sess, "is_important", False)),
        "active": bool(active),
        "status": status,
    }


def _chat_run_options(body: dict) -> dict:
    """Translate the phone's capability toggles into chat_stream form fields,
    mirroring the desktop's static/js/chat.js:
      - agent mode is the master switch that gives the model tools;
      - web search is a pre-search augmentation in chat mode (use_web) and a
        live tool in agent mode (allow_web_search);
      - terminal (bash) is an agent-only tool, so enabling it implies agent mode.
    Anything omitted/false leaves the run as a plain chat, same as before."""
    agent = bool(body.get("agent"))
    web = bool(body.get("web"))
    terminal = bool(body.get("terminal"))
    if terminal:
        agent = True
    extra = {"mode": "agent" if agent else "chat"}
    if web:
        extra["allow_web_search" if agent else "use_web"] = "true"
    if terminal:
        extra["allow_bash"] = "true"
    # Attachment ids from POST /api/companion/upload; chat_stream parses this as
    # a JSON list and resolves each id owner-scoped.
    atts = body.get("attachments")
    if isinstance(atts, list) and atts:
        import json as _json
        extra["attachments"] = _json.dumps([str(a) for a in atts])
    return extra


def _has_attachments(body: dict) -> bool:
    atts = body.get("attachments")
    return isinstance(atts, list) and len(atts) > 0


async def _fire_chat_run(
    request: Request, owner: str, sid: str, message: str, extra: dict | None = None
) -> None:
    """Kick off the detached chat run for `sid` and return once it's registered.

    Internal loopback to /api/chat_stream -- the SAME path the desktop uses, so
    the run reuses the real pipeline (models, tools, reasoning, message saving)
    and is owned by `owner` (stamped via X-Odysseus-Owner). chat_stream calls
    agent_runs.start before it streams, so by the time response headers arrive
    the run is detached and self-draining; we close the loopback immediately
    (closing an SSE never stops a detached run) and the phone watches via
    /sessions/{id}/stream. Shared by start_session (new chat) and send_message
    (follow-up turn) so both go through identical machinery.
    """
    import httpx

    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

    port = request.url.port or _pairing.default_port()
    url = f"http://127.0.0.1:{port}/api/chat_stream"
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN, "X-Odysseus-Owner": owner}
    data = {"message": message, "session": sid}
    if extra:
        data.update(extra)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            # Opening the stream sends the request and waits for headers; that is
            # enough to guarantee the run started. We never read the body.
            async with client.stream("POST", url, data=data, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise HTTPException(502, f"chat_stream returned {resp.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"could not start the run: {e}")


async def _proxy_internal(request: Request, owner: str, method: str, path: str,
                          *, params=None, json_body=None) -> Response:
    """Call an existing in-app route AS `owner` and pass its response straight
    back to the phone.

    Loopback to 127.0.0.1 with the internal-tool header + X-Odysseus-Owner --
    the SAME owner-impersonation the agent tool layer uses (app.py AuthMiddleware
    maps it to request.state.current_user "for notes/calendar/etc."). This lets
    the companion reuse the desktop's owner-scoped email/calendar/notes/tasks
    routes without re-implementing them or widening the chat-scoped bearer
    token. The phone never gets a generic proxy: each companion endpoint pins a
    fixed internal path, so only the intended actions are reachable.
    """
    import httpx

    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN

    port = request.url.port or _pairing.default_port()
    url = f"http://127.0.0.1:{port}{path}"
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN, "X-Odysseus-Owner": owner}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0)) as client:
            resp = await client.request(method, url, params=params, json=json_body, headers=headers)
    except Exception as e:
        raise HTTPException(502, f"internal request failed: {e}")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


def _resolve_endpoint_url(owner, endpoint_id: str):
    """Resolve an owner-visible, enabled endpoint id to (chat_url, base_url,
    api_key). 404s if the endpoint is missing or owned by someone else. Shared
    by the new-chat and model-switch paths."""
    from core.database import SessionLocal, ModelEndpoint
    from src.endpoint_resolver import build_chat_url, normalize_base

    db = SessionLocal()
    try:
        ep = db.query(ModelEndpoint).filter(
            ModelEndpoint.id == endpoint_id,
            ModelEndpoint.is_enabled == True,  # noqa: E712
        ).first()
        if ep is None or not owner_can_see(ep.owner, owner):
            raise HTTPException(404, "Model endpoint not found")
        return build_chat_url(normalize_base(ep.base_url)), (ep.base_url or ""), (ep.api_key or "")
    finally:
        db.close()


def _switch_session_model(session, sid: str, owner, endpoint_id: str, model: str) -> None:
    """Switch a session's model/endpoint mid-conversation, mirroring the desktop
    PATCH /session/{sid} path: resolve the endpoint owner-scoped, then update
    model + endpoint_url + auth headers both in memory and in the DB row so the
    next run (and the desktop UI) use the new model."""
    from datetime import datetime

    from core.database import Session as DbSession, SessionLocal
    from src.endpoint_resolver import build_headers

    chat_url, base_url, api_key = _resolve_endpoint_url(owner, endpoint_id)
    session.model = model
    session.endpoint_url = chat_url
    session.headers = build_headers(api_key, base_url) if api_key else {}
    db = SessionLocal()
    try:
        db_session = db.query(DbSession).filter(DbSession.id == sid).first()
        if db_session:
            db_session.model = model
            db_session.endpoint_url = chat_url
            db_session.headers = session.headers or {}
            db_session.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def mint_pairing_token(owner: str, invalidate=None) -> tuple[str, str]:
    """Mint a pairing token AND invalidate the auth middleware's in-memory token
    cache, so the new token is accepted on the very next request without a server
    restart. Returns (token_id, raw_token); the raw token is shown once.

    `invalidate` is the app's request.app.state.invalidate_token_cache callable
    (passed in so this stays a pure, testable unit).
    """
    token_id, raw_token = _pairing.mint_token(owner)
    if callable(invalidate):
        invalidate()
    return token_id, raw_token


def setup_companion_routes(session_manager=None, upload_handler=None) -> APIRouter:
    router = APIRouter(prefix="/api/companion", tags=["companion"])

    @router.get("/ping")
    def ping(request: Request):
        """Cheap, auth-validated health check. A 200 with ok=true confirms the
        host/port and credential are valid; middleware returns 401 otherwise."""
        from core.constants import APP_VERSION
        return {
            "ok": True,
            "name": "odysseus",
            "version": APP_VERSION,
            "auth": "token" if getattr(request.state, "api_token", False) else "session",
        }

    @router.get("/info")
    def info(request: Request):
        """Server identity + coarse capability flags. `owner` is the caller's own
        identity (the token's owner for bearer callers)."""
        from core.constants import APP_VERSION
        return {
            "name": "odysseus",
            "version": APP_VERSION,
            "owner": token_owner(request),
            "capabilities": {"chat": True, "streaming": True},
        }

    @router.get("/models")
    def models(request: Request):
        """LLM model endpoints the CALLER can use.

        The stock /api/models route scopes to get_current_user, which for a
        bearer token is the sandboxed pseudo-user "api" (owns nothing). Here we
        scope to the token's real owner instead, plus legacy null-owner shared
        rows -- the same rule as owner_filter. Read-only; never returns api_key
        material.
        """
        import json as _json

        from core.database import SessionLocal, ModelEndpoint
        from src.endpoint_resolver import build_chat_url

        owner = token_owner(request)
        out = []
        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(
                ModelEndpoint.is_enabled == True,  # noqa: E712
                (ModelEndpoint.model_type == "llm") | (ModelEndpoint.model_type == None),  # noqa: E711
            )
            if owner:
                q = q.filter((ModelEndpoint.owner == owner) | (ModelEndpoint.owner == None))  # noqa: E711
            for ep in q.all():
                if not owner_can_see(ep.owner, owner):
                    continue
                try:
                    model_ids = _json.loads(ep.cached_models) if ep.cached_models else []
                except (ValueError, TypeError):
                    model_ids = []
                try:
                    hidden = set(_json.loads(ep.hidden_models)) if ep.hidden_models else set()
                except (ValueError, TypeError):
                    hidden = set()
                model_ids = [m for m in model_ids if m not in hidden]
                try:
                    chat_url = build_chat_url(ep.base_url)
                except Exception:
                    chat_url = ep.base_url
                out.append({
                    "endpoint_id": ep.id,
                    "name": ep.name,
                    "endpoint_url": chat_url,
                    "models": model_ids,
                    "supports_tools": ep.supports_tools,
                })
        finally:
            db.close()
        return {"endpoints": out}

    @router.post("/upload")
    async def upload(request: Request, files: list[UploadFile] = File(...)):
        """Accept files from the phone (camera/gallery) and return attachment ids
        to hand to a chat send (POST /sessions or /sessions/{id}/message).

        Owner-stamped with the token's REAL owner, not the sandbox "api" user --
        chat_handler resolves attachment ids with an exact owner match (no admin
        shortcut on that path), so an upload owned by "api" would be invisible to
        the owner's session. Mirrors the stock /api/upload route (same handler,
        same per-IP concurrency guard); we just attribute it to the right owner
        and scope it under the companion prefix so the phone can reach it with a
        bearer token. Files are served back via the existing /api/upload/{id}.
        """
        import time

        from src.upload_handler import count_recent_uploads

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")
        if upload_handler is None:
            raise HTTPException(503, "Uploads are not available on this server")
        if not files:
            raise HTTPException(400, "No files uploaded")

        client_ip = request.client.host if request.client else "unknown"
        recent = count_recent_uploads(
            upload_handler.upload_rate_log.get(client_ip, []), time.time()
        )
        if recent >= upload_handler.max_concurrent_uploads:
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
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                })
            except HTTPException:
                raise
            except Exception:
                continue
        if not out:
            raise HTTPException(500, "All file uploads failed")
        return {"files": out}

    @router.get("/upload/{file_id}")
    def download_attachment(request: Request, file_id: str, thumb: int = 0):
        """Serve an attachment owner-checked for the token's REAL owner.

        The stock /api/upload/{id} resolves the caller via get_current_user,
        which for a bearer token is the sandbox "api" user -- so the owner check
        there 404s a phone request. Here we resolve with token_owner and reuse
        upload_handler.resolve_upload (the same owner-aware lookup the chat path
        uses). `?thumb=1` returns a small cached JPEG for images so the chat
        history isn't pulling full-resolution photos to show them tiny.
        """
        import os

        from fastapi.responses import FileResponse

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")
        if upload_handler is None:
            raise HTTPException(503, "Uploads are not available on this server")

        info = upload_handler.resolve_upload(file_id, owner=owner)
        if not info:
            raise HTTPException(404, "File not found")
        path = info["path"]
        mime = info.get("mime") or "application/octet-stream"
        headers = {"X-Content-Type-Options": "nosniff"}

        if thumb and mime.startswith("image/"):
            try:
                from PIL import Image, ImageOps

                root = os.path.dirname(path)
                thumb_dir = os.path.join(getattr(upload_handler, "upload_dir", root), ".thumbs")
                os.makedirs(thumb_dir, exist_ok=True)
                thumb_path = os.path.join(thumb_dir, file_id + ".jpg")
                if (not os.path.exists(thumb_path)
                        or os.path.getmtime(thumb_path) < os.path.getmtime(path)):
                    im = ImageOps.exif_transpose(Image.open(path))
                    im.thumbnail((320, 320))
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    im.save(thumb_path, "JPEG", quality=80)
                return FileResponse(thumb_path, media_type="image/jpeg", headers=headers)
            except Exception:
                pass  # fall back to the full image

        return FileResponse(
            path, media_type=mime, filename=info.get("name") or file_id, headers=headers
        )

    @router.get("/fs/browse")
    def fs_browse(request: Request, path: str = ""):
        """Browse the PC filesystem so the phone can pick a file to attach.

        ADMIN-ONLY, the same gate as /api/workspace/browse: enumerating the host
        filesystem is sensitive, so a non-admin (or an ownerless token) is
        refused. Unlike workspace browse this lists files too, not just dirs.
        Hidden entries and symlinked dirs are skipped; paths are canonicalised so
        the client navigates real directories.
        """
        import os

        from src.tool_security import owner_is_admin_or_single_user

        owner = token_owner(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(403, "File browsing is admin-only")

        target = os.path.realpath(os.path.expanduser(path.strip() or "~"))
        if not os.path.isdir(target):
            target = os.path.realpath(os.path.expanduser("~"))

        dirs, files = [], []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        if entry.name.startswith("."):
                            continue
                        child = os.path.join(target, entry.name)
                        if entry.is_dir(follow_symlinks=False):
                            dirs.append({"name": entry.name, "path": child})
                        elif entry.is_file(follow_symlinks=False):
                            try:
                                size = entry.stat(follow_symlinks=False).st_size
                            except OSError:
                                size = 0
                            files.append({"name": entry.name, "path": child, "size": size})
                    except OSError:
                        continue
        except (PermissionError, OSError):
            pass

        parent = os.path.dirname(target)
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "dirs": sorted(dirs, key=lambda d: d["name"].lower()),
            "files": sorted(files, key=lambda f: f["name"].lower()),
        }

    @router.post("/fs/attach")
    async def fs_attach(request: Request):
        """Copy a PC file (picked via /fs/browse) into a normal attachment so it
        flows through the SAME owner-checked pipeline as a phone upload. Returns
        the attachment id for a chat send. ADMIN-ONLY, owner-stamped.

        Reuses upload_handler.save_upload (size/type validation, dedup,
        thumbnails) by wrapping the local file in a minimal UploadFile-like shim
        -- save_upload only reads .file and .filename.
        """
        import os

        from src.tool_security import owner_is_admin_or_single_user

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(403, "File access is admin-only")
        if upload_handler is None:
            raise HTTPException(503, "Uploads are not available on this server")

        try:
            body = await request.json()
        except Exception:
            body = {}
        raw = (body.get("path") or "").strip()
        if not raw:
            raise HTTPException(400, "path is required")
        path = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isfile(path):
            raise HTTPException(404, "File not found")

        class _LocalUpload:
            def __init__(self, p):
                self.filename = os.path.basename(p)
                self.file = open(p, "rb")

        client_ip = request.client.host if request.client else "unknown"
        up = _LocalUpload(path)
        try:
            meta = upload_handler.save_upload(up, client_ip, owner=owner)
        finally:
            try:
                up.file.close()
            except Exception:
                pass
        return {
            "id": meta["id"],
            "name": meta["name"],
            "mime": meta["mime"],
            "size": meta["size"],
            "width": meta.get("width"),
            "height": meta.get("height"),
        }

    @router.get("/sessions")
    def list_sessions(request: Request):
        """The caller's own sessions, annotated with live run state.

        Owner-scoped via token_owner: a paired phone sees exactly the sessions
        the owner sees on the desktop UI -- never another user's. An unresolved
        owner (no cookie user, ownerless token) gets an EMPTY list, never the
        global set -- session_manager.get_sessions_for_user(None) returns ALL
        sessions, so we must not pass None through.

        Each row carries agent_runs is_active/get_status so the client knows
        which sessions are still generating and can reconnect to
        /sessions/{id}/stream without a probe request. Read-only; accepts a
        cookie session or a chat-scoped bearer token (same as /info, /models).
        """
        from src import agent_runs
        from routes.session_routes import _public_model, _HIDDEN_SYSTEM_SESSION_NAMES

        owner = token_owner(request)
        if not owner:
            return {"sessions": []}

        sm = _resolve_session_manager(session_manager)
        out = []
        for sess in sm.get_sessions_for_user(owner).values():
            if getattr(sess, "archived", False):
                continue
            name = (sess.name or "").strip()
            if name in ("Nobody", "Incognito") or name in _HIDDEN_SYSTEM_SESSION_NAMES:
                continue
            out.append(session_summary(
                sess,
                agent_runs.is_active(sess.id),
                agent_runs.get_status(sess.id),
                public_model=_public_model(sess.name, sess.model),
            ))
        # Live runs first so the phone surfaces in-flight work at the top; the
        # client is free to re-sort.
        out.sort(key=lambda s: not s["active"])
        return {"sessions": out}

    @router.post("/sessions")
    async def start_session(request: Request):
        """Start a NEW chat from the phone and return its session id.

        Creates an owner-scoped session, then kicks off the SAME detached run the
        desktop uses by making an internal loopback POST to /api/chat_stream. That
        handler calls agent_runs.start BEFORE it streams, so by the time we get
        response headers the run is registered and self-draining -- we close the
        loopback immediately (closing an SSE never stops a detached run) and the
        phone watches via /sessions/{id}/stream. We reuse the real chat pipeline
        (models, tools, reasoning, message saving) rather than duplicate it; no
        new LLM logic lives here.

        Body (JSON): {message, endpoint_id, model}. endpoint_id/model come from
        /api/companion/models. Owner-scoped: the session is owned by the token's
        real owner, so it shows in the caller's list and passes ownership checks.
        """
        import uuid as _uuid

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")

        try:
            body = await request.json()
        except Exception:
            body = {}
        message = (body.get("message") or "").strip()
        model = (body.get("model") or "").strip()
        endpoint_id = (body.get("endpoint_id") or "").strip()
        if not message and not _has_attachments(body):
            raise HTTPException(400, "message is required")
        if not endpoint_id or not model:
            raise HTTPException(400, "endpoint_id and model are required")

        # Resolve the endpoint to a chat URL, owner-scoped (own or shared rows).
        endpoint_url, _, _ = _resolve_endpoint_url(owner, endpoint_id)

        # Create the (empty) session first; chat_stream adds the user message.
        sm = _resolve_session_manager(session_manager)
        sid = str(_uuid.uuid4())
        name = (message[:40] or "Photo").strip()
        sm.create_session(sid, name, endpoint_url, model, owner=owner)

        await _fire_chat_run(request, owner, sid, message, _chat_run_options(body))
        return {"session_id": sid, "name": name}

    @router.get("/sessions/{session_id}/messages")
    def session_messages(request: Request, session_id: str):
        """Saved conversation for one session, so the phone can open ANY session
        -- not only one with a live run. Owner-checked. Returns user/assistant
        turns; reasoning is already stripped from saved messages (same as the
        desktop), so this is the finished answer text."""
        from routes.session_routes import _verify_session_owner, _content_to_text

        sm = _resolve_session_manager(session_manager)
        _verify_session_owner(request, session_id, sm)
        try:
            sess = sm.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        out = []
        for m in getattr(sess, "history", []) or []:
            role = getattr(m, "role", None)
            if role not in ("user", "assistant"):
                continue
            content = getattr(m, "content", "")
            if not isinstance(content, str):
                content = _content_to_text(content)
            row = {"role": role, "content": content}
            # Surface attachments saved on the user turn so the phone can render
            # the actual thumbnails (via GET /api/companion/upload/{id}) instead
            # of the "[Image: name]" text the model sees.
            meta = getattr(m, "metadata", None) or {}
            atts = meta.get("attachments")
            if isinstance(atts, list) and atts:
                row["attachments"] = [
                    {"id": a.get("id"), "name": a.get("name") or a.get("id"), "mime": a.get("mime", "")}
                    for a in atts
                    if isinstance(a, dict) and a.get("id")
                ]
            out.append(row)
        # model/name let the phone default its in-chat model picker and title
        # without a second round-trip to the sessions list.
        return {"messages": out, "model": getattr(sess, "model", "") or "", "name": sess.name}

    @router.post("/sessions/{session_id}/message")
    async def send_message(request: Request, session_id: str):
        """Send a follow-up turn into an EXISTING session from the phone.

        Same internal-loopback machinery as POST /sessions (new chat), but the
        session already exists, so we just verify ownership, optionally switch
        the model (body endpoint_id+model -- the phone's in-chat model picker,
        mirroring the desktop), refuse if a run is already in flight, then fire
        the detached run. The phone re-attaches to /sessions/{id}/stream to watch
        it. Owner-checked; no new LLM logic.
        """
        from src import agent_runs
        from routes.session_routes import _verify_session_owner

        owner = token_owner(request)
        if not owner:
            raise HTTPException(401, "Could not resolve an owner for this token")

        sm = _resolve_session_manager(session_manager)
        _verify_session_owner(request, session_id, sm)
        try:
            sess = sm.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        if agent_runs.is_active(session_id):
            raise HTTPException(409, "A run is already in progress for this session")

        try:
            body = await request.json()
        except Exception:
            body = {}
        message = (body.get("message") or "").strip()
        if not message and not _has_attachments(body):
            raise HTTPException(400, "message is required")

        # Optional mid-chat model switch (the in-chat picker). Both must be given.
        model = (body.get("model") or "").strip()
        endpoint_id = (body.get("endpoint_id") or "").strip()
        if model and endpoint_id and model != getattr(sess, "model", ""):
            _switch_session_model(sess, session_id, owner, endpoint_id, model)

        if not getattr(sess, "model", "").strip():
            raise HTTPException(400, "This session has no model selected")

        await _fire_chat_run(request, owner, session_id, message, _chat_run_options(body))
        return {"ok": True}

    @router.get("/sessions/{session_id}/stream")
    async def stream_session(request: Request, session_id: str):
        """Live SSE for one session -- the phone's "watch what it's doing" view.

        Thin wrapper over the SAME detached-run machinery the desktop UI uses
        (agent_runs.subscribe replays the buffer so far, then streams live), so
        a phone that connects mid-run picks up where it is, and disconnecting
        does NOT stop the run. Owner-checked via _verify_session_owner (which
        resolves the bearer token's real owner), so a paired phone can only
        watch its own sessions. 404 if no run is active for this session.
        """
        from src import agent_runs
        from routes.session_routes import _verify_session_owner

        _verify_session_owner(request, session_id, _resolve_session_manager(session_manager))
        if not agent_runs.is_active(session_id):
            raise HTTPException(404, "No active run for this session")
        return StreamingResponse(agent_runs.subscribe(session_id), media_type="text/event-stream")

    @router.post("/sessions/{session_id}/stop")
    async def stop_session(request: Request, session_id: str):
        """The phone's Stop/interrupt button. Cancels the detached run server-
        side (closing the SSE alone does not, by design). Owner-checked the same
        way as the stream. Returns {stopped: bool} -- false if nothing was
        running, which the client can treat as already-stopped."""
        from src import agent_runs
        from routes.session_routes import _verify_session_owner

        _verify_session_owner(request, session_id, _resolve_session_manager(session_manager))
        return {"stopped": agent_runs.stop(session_id)}

    # ---------------------------------------------------------------- #
    # Tools: thin owner-impersonating proxies over the desktop's existing
    # owner-scoped routes (see _proxy_internal). Each pins a fixed path.
    # ---------------------------------------------------------------- #
    @router.get("/email/accounts")
    async def email_accounts(request: Request):
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/email/accounts")

    @router.get("/email/list")
    async def email_list(
        request: Request,
        folder: str = "INBOX",
        limit: int = 50,
        offset: int = 0,
        filter: str = "all",
        account_id: str | None = None,
    ):
        params = {"folder": folder, "limit": limit, "offset": offset, "filter": filter}
        if account_id:
            params["account_id"] = account_id
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/email/list", params=params)

    @router.get("/email/read/{uid}")
    async def email_read(request: Request, uid: str, folder: str = "INBOX", account_id: str | None = None):
        params = {"folder": folder}
        if account_id:
            params["account_id"] = account_id
        return await _proxy_internal(
            request, _require_owner(request), "GET", f"/api/email/read/{uid}", params=params
        )

    @router.post("/email/{uid}/flag")
    async def email_flag(request: Request, uid: str, action: str, folder: str = "INBOX", account_id: str | None = None):
        """Mark read/unread or archive. `action` is mark-read|mark-unread|archive."""
        if action not in ("mark-read", "mark-unread", "archive"):
            raise HTTPException(400, "unknown action")
        params = {"folder": folder}
        if account_id:
            params["account_id"] = account_id
        return await _proxy_internal(
            request, _require_owner(request), "POST", f"/api/email/{action}/{uid}", params=params
        )

    @router.post("/email/send")
    async def email_send(request: Request):
        owner = _require_owner(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await _proxy_internal(request, owner, "POST", "/api/email/send", json_body=body)

    @router.post("/email/ai-reply")
    async def email_ai_reply(request: Request):
        owner = _require_owner(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await _proxy_internal(request, owner, "POST", "/api/email/ai-reply", json_body=body)

    @router.post("/email/summarize")
    async def email_summarize(request: Request):
        owner = _require_owner(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await _proxy_internal(request, owner, "POST", "/api/email/summarize", json_body=body)

    @router.get("/calendar/calendars")
    async def calendar_calendars(request: Request):
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/calendar/calendars")

    @router.get("/calendar/events")
    async def calendar_events(request: Request, start: str, end: str, calendar: str = ""):
        params = {"start": start, "end": end}
        if calendar:
            params["calendar"] = calendar
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/calendar/events", params=params)

    @router.get("/notes")
    async def notes_list(request: Request, archived: bool | None = None, label: str | None = None):
        params = {}
        if archived is not None:
            params["archived"] = str(archived).lower()
        if label:
            params["label"] = label
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/notes", params=params)

    @router.post("/notes")
    async def notes_create(request: Request):
        owner = _require_owner(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return await _proxy_internal(request, owner, "POST", "/api/notes", json_body=body)

    @router.get("/tasks")
    async def tasks_list(request: Request, status: str | None = None):
        params = {}
        if status:
            params["status"] = status
        return await _proxy_internal(request, _require_owner(request), "GET", "/api/tasks", params=params)

    @router.post("/tasks/{task_id}/{action}")
    async def tasks_action(request: Request, task_id: str, action: str):
        """Pause, resume, or run a scheduled task."""
        if action not in ("pause", "resume", "run"):
            raise HTTPException(400, "unknown action")
        return await _proxy_internal(
            request, _require_owner(request), "POST", f"/api/tasks/{task_id}/{action}"
        )

    @router.get("/pair")
    def pair_page(request: Request):
        """Admin-only pairing page. Renders a form that POSTs to mint a code.

        A GET never mints a credential: SameSite=Lax session cookies ride
        top-level GET navigations, so minting on GET would be triggerable by a
        link or <img> (CSRF). The actual mint is the POST handler below.
        """
        require_admin(request)
        page = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pair a device</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:48px auto;padding:0 20px;color:#e8e8e8;background:#16161a}
  .card{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:28px;text-align:center}
  button{background:#7c9cff;color:#0e0e12;border:none;border-radius:10px;padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer}
</style></head>
<body><div class="card">
  <h2>Pair a device</h2>
  <p>Generate a one-time pairing code (a chat-scoped API token) for a LAN client.</p>
  <form method="POST" action="/api/companion/pair">
    <button type="submit">Generate pairing code</button>
  </form>
  <p style="color:#8a8a96;font-size:12px;margin-top:18px">Admin only. Each code mints a new token, shown once. Manage or revoke under Settings &rarr; API tokens.</p>
</div></body></html>"""
        return HTMLResponse(page)

    @router.post("/pair")
    def pair_create(request: Request):
        """Mint a pairing code. Admin-cookie only; CSRF-safe because the
        SameSite=Lax session cookie is not sent on a cross-site POST (same
        protection as POST /api/tokens). Minting invalidates the token cache so
        the code works immediately, no restart. `?format=json` returns the
        payload for an in-app pairing screen."""
        require_admin(request)
        owner = get_current_user(request)
        invalidate = getattr(request.app.state, "invalidate_token_cache", None)
        token_id, raw_token = mint_pairing_token(owner, invalidate)

        hosts = _pairing.lan_ip_candidates()
        host = hosts[0] if hosts else "127.0.0.1"
        port = request.url.port or _pairing.default_port()
        payload = _pairing.pairing_payload(host, port, raw_token)
        qr = _pairing.pairing_qr_png_data_uri(payload)
        qr_ok = bool(qr and qr.startswith("data:image/png;base64,"))

        if (request.query_params.get("format") or "").lower() == "json":
            return {
                "host": host,
                "port": port,
                "token": raw_token,
                "token_id": token_id,
                "hosts": hosts,
                "payload": payload,
                "qr": qr if qr_ok else None,
            }

        import json as _json
        payload_json = _json.dumps(payload, separators=(",", ":"))
        # Only ever emit a known PNG data-URI into the src; every other value is
        # html.escaped.
        qr_block = (
            f'<img src="{html.escape(qr)}" alt="Pairing QR" width="260" height="260">'
            if qr_ok else "<p><em>QR rendering unavailable -- enter the details manually.</em></p>"
        )
        page = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pairing code</title>
<style>
  body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;color:#e8e8e8;background:#16161a}}
  .card{{background:#1f1f25;border:1px solid #2c2c35;border-radius:14px;padding:24px;text-align:center}}
  code{{background:#0e0e12;padding:2px 6px;border-radius:6px;word-break:break-all}}
  .row{{text-align:left;margin:10px 0;font-size:14px;color:#bdbdc7}}
  .warn{{color:#e0a85e;font-size:13px;margin-top:18px}}
</style></head>
<body><div class="card">
  <h2>Pairing code</h2>
  {qr_block}
  <div class="row"><strong>Host:</strong> <code>{html.escape(host)}</code></div>
  <div class="row"><strong>Port:</strong> <code>{html.escape(str(port))}</code></div>
  <div class="row"><strong>Token:</strong> <code>{html.escape(raw_token)}</code></div>
  <div class="row"><strong>Payload:</strong> <code>{html.escape(payload_json)}</code></div>
  <p class="warn">Shown once. This grants chat access to your Odysseus; revoke it
  in Settings &rarr; API tokens (id <code>{html.escape(token_id)}</code>). The
  device must be on the same network, and the server must bind to your LAN.</p>
</div></body></html>"""
        return HTMLResponse(page)

    return router
