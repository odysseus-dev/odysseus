"""Codex integration routes.

These are small HTTP surfaces intended for the Codex plugin/MCP bridge. They
reuse existing Odysseus helpers and enforce API-token scopes before touching
user data.
"""

import asyncio
import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.auth_helpers import require_authenticated_request, require_user
from src.tool_implementations import do_manage_notes
from src.constants import COOKBOOK_STATE_FILE
from routes._validators import validate_remote_host, validate_ssh_port


COOKBOOK_READ_SCOPES = {"cookbook:read", "cookbook:launch"}
COOKBOOK_LAUNCH_SCOPES = {"cookbook:launch"}
TODO_READ_SCOPES = {"todos:read", "todos:write"}
TODO_WRITE_SCOPES = {"todos:write"}
EMAIL_READ_SCOPES = {"email:read", "email:draft", "email:send"}
EMAIL_DRAFT_SCOPES = {"email:draft", "email:send"}
EMAIL_SEND_SCOPES = {"email:send"}
MEMORY_READ_SCOPES = {"memory:read", "memory:write"}
MEMORY_WRITE_SCOPES = {"memory:write"}
CALENDAR_READ_SCOPES = {"calendar:read", "calendar:write"}
CALENDAR_WRITE_SCOPES = {"calendar:write"}
DOCS_READ_SCOPES = {"documents:read", "documents:write"}
DOCS_WRITE_SCOPES = {"documents:write"}
WRITE_ACTIONS = {"add", "create", "new", "save", "remind", "update", "delete", "toggle_item", "remove", "remove_item"}


def _ssh_prefix_for_task(task: dict) -> tuple[str, str]:
    """Resolve a cookbook task's stored SSH target into ``(host, port_flag)``.

    ``host`` is ``""`` for a local task. ``remoteHost`` / ``sshPort`` come from
    cookbook_state.json and get interpolated into an ``ssh`` command string, so
    validate them the same way the cookbook routes do. A tampered entry with
    shell metacharacters in ``remoteHost`` is rejected with 400 rather than
    injected.
    """
    host = validate_remote_host((task.get("remoteHost") or "").strip() or None) or ""
    ssh_port = validate_ssh_port((task.get("sshPort") or "").strip() or None) or ""
    port_flag = f"-p {ssh_port} " if ssh_port and ssh_port != "22" else ""
    return host, port_flag


async def _as_owner(request: Request, owner: str, fn, *args, **kwargs):
    """Run an existing route handler with request.state.current_user temporarily
    set to ``owner`` so its internal get_current_user/require_user calls see
    the scope-gated owner (not the "api" pseudo-user the bearer middleware sets).
    Restores the original value when done. Works for sync and async handlers."""
    orig = getattr(request.state, "current_user", None)
    orig_api_token = getattr(request.state, "api_token", None)
    request.state.current_user = owner
    request.state.api_token = False
    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    finally:
        request.state.current_user = orig
        if orig_api_token is None:
            try:
                delattr(request.state, "api_token")
            except AttributeError:
                pass
        else:
            request.state.api_token = orig_api_token


def _scope_owner(request: Request, allowed: set[str]) -> str:
    """Return the data owner if the caller is allowed for this Codex action."""
    if getattr(request.state, "api_token", False):
        scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        if not scopes.intersection(allowed):
            required = " or ".join(sorted(allowed))
            raise HTTPException(403, f"API token missing required scope: {required}")
        owner = getattr(request.state, "api_token_owner", None)
        if not owner:
            raise HTTPException(403, "API token has no owner")
        return owner
    return require_user(request)


def _find_endpoint(router: APIRouter | None, method: str, path: str):
    if router is None:
        return None
    for route in getattr(router, "routes", []):
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    return None


def setup_codex_routes(
    email_router: APIRouter | None = None,
    memory_router: APIRouter | None = None,
    calendar_router: APIRouter | None = None,
    document_router: APIRouter | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/codex", tags=["codex"])
    email_list_endpoint = _find_endpoint(email_router, "GET", "/api/email/list")
    email_read_endpoint = _find_endpoint(email_router, "GET", "/api/email/read/{uid}")
    email_send_endpoint = _find_endpoint(email_router, "POST", "/api/email/send")
    email_draft_endpoint = _find_endpoint(email_router, "POST", "/api/email/draft")
    memory_list_endpoint = _find_endpoint(memory_router, "GET", "/api/memory")
    memory_add_endpoint = _find_endpoint(memory_router, "POST", "/api/memory/add")
    calendar_list_events = _find_endpoint(calendar_router, "GET", "/api/calendar/events")
    calendar_create_event = _find_endpoint(calendar_router, "POST", "/api/calendar/events")
    documents_library_endpoint = _find_endpoint(document_router, "GET", "/api/documents/library")
    documents_get_endpoint = _find_endpoint(document_router, "GET", "/api/document/{doc_id}")
    documents_create_endpoint = _find_endpoint(document_router, "POST", "/api/document")

    @router.get("/capabilities")
    def capabilities(request: Request):
        token_scopes = set(getattr(request.state, "api_token_scopes", []) or [])
        has_token = bool(getattr(request.state, "api_token", False))
        def scoped(allowed):
            return bool(token_scopes.intersection(allowed)) if has_token else True
        return {
            "integration": "codex",
            "token_scopes": sorted(token_scopes),
            "tools": {
                "todos": {
                    "read": scoped(TODO_READ_SCOPES),
                    "write": scoped(TODO_WRITE_SCOPES),
                    "actions": ["list", "add", "update", "delete", "toggle_item"],
                },
                "email": {
                    "read": scoped(EMAIL_READ_SCOPES),
                    "draft": scoped(EMAIL_DRAFT_SCOPES),
                    "send": scoped(EMAIL_SEND_SCOPES),
                    "actions": ["list", "read", "draft", "send"],
                },
                "memory": {
                    "read": scoped(MEMORY_READ_SCOPES),
                    "write": scoped(MEMORY_WRITE_SCOPES),
                    "actions": ["list", "add", "delete"],
                    "available": memory_list_endpoint is not None,
                },
                "calendar": {
                    "read": scoped(CALENDAR_READ_SCOPES),
                    "write": scoped(CALENDAR_WRITE_SCOPES),
                    "actions": ["list_events", "create_event", "delete_event"],
                    "available": calendar_list_events is not None,
                },
                "documents": {
                    "read": scoped(DOCS_READ_SCOPES),
                    "write": scoped(DOCS_WRITE_SCOPES),
                    "actions": ["library", "read", "create", "delete"],
                    "available": documents_library_endpoint is not None,
                },
                "cookbook": {
                    "read": scoped(COOKBOOK_READ_SCOPES),
                    "launch": scoped(COOKBOOK_LAUNCH_SCOPES),
                    "actions": ["tasks", "servers", "output", "serve", "stop"],
                },
            },
            "safety": {
                "email_send_requires_confirmation": True,
                "destructive_actions_should_confirm": True,
            },
        }

    @router.get("/plugin.zip")
    def plugin_zip(request: Request):
        require_authenticated_request(request)
        root = Path(__file__).resolve().parent.parent / "integrations" / "codex"
        if not root.exists():
            raise HTTPException(404, "Codex plugin bundle not found")
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                zf.write(path, Path("odysseus") / path.relative_to(root))
        buf.seek(0)
        headers = {"Content-Disposition": 'attachment; filename="odysseus-codex-plugin.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    @router.get("/todos")
    async def list_todos(request: Request, archived: bool = False, label: str | None = None):
        owner = _scope_owner(request, TODO_READ_SCOPES)
        args: dict[str, Any] = {"action": "list", "archived": archived}
        if label:
            args["label"] = label
        return await do_manage_notes(json.dumps(args), owner=owner)

    @router.post("/todos")
    async def manage_todos(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        action = str(body.get("action") or "add").replace("-", "_").strip().lower()
        allowed = TODO_WRITE_SCOPES if action in WRITE_ACTIONS else TODO_READ_SCOPES
        owner = _scope_owner(request, allowed)
        args = dict(body)
        args["action"] = action
        return await do_manage_notes(json.dumps(args), owner=owner)

    @router.get("/emails")
    async def list_emails(
        request: Request,
        folder: str = "INBOX",
        limit: int = 10,
        offset: int = 0,
        filter: str = "all",
        from_addr: str | None = None,
        account_id: str | None = None,
        has_attachments: int = 0,
    ):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        if email_list_endpoint is None:
            raise HTTPException(503, "Email integration is not available")
        limit = max(1, min(int(limit or 10), 50))
        offset = max(0, int(offset or 0))
        if account_id:
            from routes.email_helpers import _assert_owns_account

            _assert_owns_account(account_id, owner)
        return await email_list_endpoint(
            folder=folder,
            limit=limit,
            offset=offset,
            filter=filter,
            from_addr=from_addr,
            account_id=account_id,
            has_attachments=has_attachments,
            cache_bust=None,
            owner=owner,
        )

    @router.get("/emails/{uid}")
    async def read_email(
        request: Request,
        uid: str,
        folder: str = "INBOX",
        account_id: str | None = None,
        mark_seen: bool = False,
    ):
        owner = _scope_owner(request, EMAIL_READ_SCOPES)
        if email_read_endpoint is None:
            raise HTTPException(503, "Email integration is not available")
        if account_id:
            from routes.email_helpers import _assert_owns_account

            _assert_owns_account(account_id, owner)
        return await email_read_endpoint(
            uid=uid,
            folder=folder,
            account_id=account_id,
            mark_seen=mark_seen,
            owner=owner,
        )

    # ── Email draft + send ────────────────────────────────────────────────
    # Both handlers in routes/email_routes.py already accept `owner=` via
    # FastAPI Depends, so we call them directly without patching state.

    @router.post("/emails/draft")
    async def codex_email_draft(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        owner = _scope_owner(request, EMAIL_DRAFT_SCOPES)
        if email_draft_endpoint is None:
            raise HTTPException(503, "Email integration is not available")
        from routes.email_routes import SendEmailRequest

        try:
            req = SendEmailRequest(**body)
        except Exception as exc:
            raise HTTPException(400, f"Invalid draft payload: {exc}")
        return await email_draft_endpoint(req=req, owner=owner)

    @router.post("/emails/send")
    async def codex_email_send(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        owner = _scope_owner(request, EMAIL_SEND_SCOPES)
        if email_send_endpoint is None:
            raise HTTPException(503, "Email integration is not available")
        from routes.email_routes import SendEmailRequest

        try:
            req = SendEmailRequest(**body)
        except Exception as exc:
            raise HTTPException(400, f"Invalid send payload: {exc}")
        return await email_send_endpoint(req=req, background_tasks=BackgroundTasks(), owner=owner)

    # ── Memory ────────────────────────────────────────────────────────────

    @router.get("/memory")
    async def codex_memory_list(request: Request):
        owner = _scope_owner(request, MEMORY_READ_SCOPES)
        if memory_list_endpoint is None:
            raise HTTPException(503, "Memory integration is not available")
        return await _as_owner(request, owner, memory_list_endpoint, request)

    @router.post("/memory")
    async def codex_memory_add(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        owner = _scope_owner(request, MEMORY_WRITE_SCOPES)
        if memory_add_endpoint is None:
            raise HTTPException(503, "Memory integration is not available")
        from src.request_models import MemoryAddRequest

        try:
            memory_data = MemoryAddRequest(
                text=str(body.get("text") or "").strip(),
                category=body.get("category", "fact"),
                source=body.get("source", "user"),
                session_id=body.get("session_id"),
            )
        except Exception as exc:
            raise HTTPException(400, f"Invalid memory payload: {exc}")
        if not memory_data.text:
            raise HTTPException(400, "Empty memory text")
        return await _as_owner(request, owner, memory_add_endpoint, request, memory_data)

    # ── Calendar ──────────────────────────────────────────────────────────

    @router.get("/calendar/events")
    async def codex_calendar_list(request: Request, start: str, end: str, calendar: str = ""):
        owner = _scope_owner(request, CALENDAR_READ_SCOPES)
        if calendar_list_events is None:
            raise HTTPException(503, "Calendar integration is not available")
        return await _as_owner(request, owner, calendar_list_events, request, start, end, calendar)

    @router.post("/calendar/events")
    async def codex_calendar_create(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        owner = _scope_owner(request, CALENDAR_WRITE_SCOPES)
        if calendar_create_event is None:
            raise HTTPException(503, "Calendar integration is not available")
        from routes.calendar_routes import EventCreate

        try:
            data = EventCreate(**body)
        except Exception as exc:
            raise HTTPException(400, f"Invalid event payload: {exc}")
        return await _as_owner(request, owner, calendar_create_event, request, data)

    # ── Documents ─────────────────────────────────────────────────────────

    @router.get("/documents")
    async def codex_documents_library(
        request: Request,
        search: str | None = None,
        language: str | None = None,
        sort: str = "recent",
        offset: int = 0,
        limit: int = 50,
        archived: bool = False,
    ):
        owner = _scope_owner(request, DOCS_READ_SCOPES)
        if documents_library_endpoint is None:
            raise HTTPException(503, "Documents integration is not available")
        return await _as_owner(
            request, owner, documents_library_endpoint,
            request, search, language, sort, offset, limit, archived,
        )

    @router.get("/documents/{doc_id}")
    async def codex_documents_get(request: Request, doc_id: str):
        owner = _scope_owner(request, DOCS_READ_SCOPES)
        if documents_get_endpoint is None:
            raise HTTPException(503, "Documents integration is not available")
        return await _as_owner(request, owner, documents_get_endpoint, request, doc_id)

    # ── DELETE endpoints so agents can clean up after themselves ──────────

    memory_delete_endpoint = _find_endpoint(memory_router, "DELETE", "/api/memory/{memory_id}")
    calendar_delete_event = _find_endpoint(calendar_router, "DELETE", "/api/calendar/events/{uid}")
    documents_delete_endpoint = _find_endpoint(document_router, "DELETE", "/api/document/{doc_id}")

    @router.delete("/memory/{memory_id}")
    async def codex_memory_delete(request: Request, memory_id: str):
        owner = _scope_owner(request, MEMORY_WRITE_SCOPES)
        if memory_delete_endpoint is None:
            raise HTTPException(503, "Memory delete not available")
        return await _as_owner(request, owner, memory_delete_endpoint, request, memory_id)

    @router.delete("/calendar/events/{uid}")
    async def codex_calendar_delete(request: Request, uid: str):
        owner = _scope_owner(request, CALENDAR_WRITE_SCOPES)
        if calendar_delete_event is None:
            raise HTTPException(503, "Calendar delete not available")
        return await _as_owner(request, owner, calendar_delete_event, request, uid)

    @router.delete("/documents/{doc_id}")
    async def codex_documents_delete(request: Request, doc_id: str):
        owner = _scope_owner(request, DOCS_WRITE_SCOPES)
        if documents_delete_endpoint is None:
            raise HTTPException(503, "Documents delete not available")
        return await _as_owner(request, owner, documents_delete_endpoint, request, doc_id)

    @router.post("/documents")
    async def codex_documents_create(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        owner = _scope_owner(request, DOCS_WRITE_SCOPES)
        if documents_create_endpoint is None:
            raise HTTPException(503, "Documents integration is not available")
        from routes.document_routes import DocumentCreate

        try:
            req = DocumentCreate(**body)
        except Exception as exc:
            raise HTTPException(400, f"Invalid document payload: {exc}")
        return await _as_owner(request, owner, documents_create_endpoint, request, req)

    # ── Cookbook surface ──
    # Lets the agent run the same launch / monitor / kill loop the user
    # would do by hand in the Cookbook UI: read the current task list +
    # tmux output, launch a serve task, stop one.  Two scopes:
    #   cookbook:read   — list tasks + tail output + list servers
    #   cookbook:launch — also start/stop serves (host shell exec)
    # `cookbook:launch` is genuinely powerful: /api/model/serve runs SSH'd
    # commands on the user's hosts. The existing _validate_serve_cmd
    # allowlist (vllm/python3/sglang/llama-server/etc., no shell metachars)
    # keeps the agent inside the same sandbox the UI uses.

    async def _run_shell(cmd: str, timeout: float = 15.0) -> dict:
        """Run a shell command, return {exit_code, stdout, stderr}."""
        import asyncio as _asyncio
        try:
            proc = await _asyncio.create_subprocess_shell(
                cmd,
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await _asyncio.wait_for(proc.communicate(), timeout=timeout)
            except _asyncio.TimeoutError:
                proc.kill()
                return {"exit_code": -1, "stdout": "", "stderr": "timed out"}
            return {
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode(errors="replace"),
                "stderr": stderr_b.decode(errors="replace"),
            }
        except Exception as exc:
            return {"exit_code": -1, "stdout": "", "stderr": str(exc)}

    def _read_cookbook_state() -> dict:
        from pathlib import Path as _Path
        import json as _json
        p = _Path(COOKBOOK_STATE_FILE)
        if not p.exists():
            return {}
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _redact_task(t: dict) -> dict:
        """Strip secrets before returning to the agent."""
        clean = {k: v for k, v in t.items() if k not in ("hf_token", "_secrets")}
        if isinstance(clean.get("payload"), dict):
            pl = clean["payload"]
            clean["payload"] = {k: v for k, v in pl.items()
                                if k not in ("hf_token", "_secrets")}
        return clean

    @router.get("/cookbook/tasks")
    async def codex_cookbook_tasks(request: Request):
        _scope_owner(request, COOKBOOK_READ_SCOPES)
        state = _read_cookbook_state()
        tasks = state.get("tasks") or []
        return {"tasks": [_redact_task(t) for t in tasks]}

    @router.get("/cookbook/servers")
    async def codex_cookbook_servers(request: Request):
        _scope_owner(request, COOKBOOK_READ_SCOPES)
        state = _read_cookbook_state()
        servers = state.get("env", {}).get("servers") or []
        # Strip ssh creds / passwords; keep only what's needed to pick a host.
        cleaned = []
        for s in servers:
            cleaned.append({
                "name": s.get("name"),
                "host": s.get("host"),
                "port": s.get("port"),
                "env": s.get("env"),
                "envPath": s.get("envPath"),
                "platform": s.get("platform"),
                "modelDirs": s.get("modelDirs"),
            })
        return {"servers": cleaned}

    @router.get("/cookbook/output/{session_id}")
    async def codex_cookbook_output(request: Request, session_id: str, tail: int = 400):
        _scope_owner(request, COOKBOOK_READ_SCOPES)
        # Defensive: session_id must be the tmux-style id we issue
        # (`serve-XXXX` / `cookbook-XXXX` / `queue-XXXX`); anything else
        # would let the agent run arbitrary `tmux capture-pane` targets.
        import re as _re
        if not _re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
            raise HTTPException(400, "Invalid session id")
        tail = max(20, min(int(tail or 400), 4000))
        # Resolve the task's host (if any) from cookbook state so we can
        # ssh to the right box, exactly as the UI does in _reconnectTask.
        state = _read_cookbook_state()
        tasks = state.get("tasks") or []
        task = next((t for t in tasks if t.get("sessionId") == session_id), None)
        if task is None:
            raise HTTPException(404, "task not found")
        host, port_flag = _ssh_prefix_for_task(task)
        # Prefer the persisted log file over the tmux pane. The pane gets
        # overwritten by the post-crash neofetch banner + bash prompt the
        # moment vllm exits; the log file is the raw stdout/stderr and
        # survives unchanged. Falls back to pane for older tasks predating
        # the tee-to-log runner change.
        log_path = f"/tmp/odysseus-tmux/{session_id}.log"
        inner = (
            f"if [ -s {log_path} ]; then tail -n {tail} {log_path}; "
            f"else tmux capture-pane -t {session_id} -p -S -{tail}; fi"
        )
        if host:
            import shlex
            cmd = f"ssh {port_flag}{host} {shlex.quote(inner)}"
        else:
            cmd = inner
        result = await _run_shell(cmd, timeout=15)
        return {
            "session_id": session_id,
            "host": host or "local",
            "exit_code": result.get("exit_code"),
            "output": result.get("stdout", ""),
            "task": _redact_task(task),
        }

    @router.post("/cookbook/serve")
    async def codex_cookbook_serve(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        _scope_owner(request, COOKBOOK_LAUNCH_SCOPES)
        # Wraps /api/model/serve with the SAME validation the UI uses.
        # _validate_serve_cmd (called inside model_serve) rejects shell
        # metachars and requires the leading binary to be in the
        # cookbook allowlist (vllm / python3 / sglang / llama-server / ...).
        from routes.cookbook_helpers import ServeRequest
        # Accept friendly aliases agents naturally reach for. Without these,
        # passing `host` silently maps to nothing and the serve runs LOCAL
        # instead of on the intended remote — exactly the bug an agent
        # would never debug on its own.
        norm = dict(body or {})
        if "host" in norm and "remote_host" not in norm:
            norm["remote_host"] = norm.pop("host")
        if "model" in norm and "repo_id" not in norm:
            norm["repo_id"] = norm.pop("model")
        if "ssh_port" not in norm and "port" in norm and (str(norm.get("port") or "").isdigit() and int(norm["port"]) >= 1000):
            # Heuristic: if `port` looks like an SSH port (≥1000) and there's
            # no explicit ssh_port, treat it as such. UI ports (8000, 8001,
            # 30000) belong inside the cmd string, not here.
            pass  # leave as-is — user's `port` here is ambiguous; skip remap.
        try:
            req = ServeRequest(**norm)
        except Exception as exc:
            raise HTTPException(400, f"Invalid serve payload: {exc}")
        serve_endpoint = _find_endpoint(None, "POST", "/api/model/serve")
        # Fall back to importing from the cookbook router registered on app.
        if serve_endpoint is None:
            from fastapi import FastAPI
            app: FastAPI = request.app
            for route in app.routes:
                if getattr(route, "path", None) == "/api/model/serve" and "POST" in getattr(route, "methods", set()):
                    serve_endpoint = route.endpoint
                    break
        if serve_endpoint is None:
            raise HTTPException(503, "model serve endpoint unavailable")
        return await serve_endpoint(request, req)

    @router.post("/cookbook/stop/{session_id}")
    async def codex_cookbook_stop(request: Request, session_id: str):
        _scope_owner(request, COOKBOOK_LAUNCH_SCOPES)
        import re as _re
        if not _re.fullmatch(r"[a-zA-Z0-9_-]+", session_id):
            raise HTTPException(400, "Invalid session id")
        state = _read_cookbook_state()
        tasks = state.get("tasks") or []
        task = next((t for t in tasks if t.get("sessionId") == session_id), None)
        host, port_flag = _ssh_prefix_for_task(task or {})
        if host:
            cmd = f"ssh {port_flag}{host} \"tmux kill-session -t {session_id}\""
        else:
            cmd = f"tmux kill-session -t {session_id}"
        result = await _run_shell(cmd, timeout=10)
        return {"session_id": session_id, "exit_code": result.get("exit_code"), "host": host or "local"}

    @router.get("/cookbook/cached")
    async def codex_cookbook_cached(request: Request, host: str | None = None):
        """List cached models on a configured server (or local if host is omitted).
        Mirrors `list_cached_models` from the chat agent so external agents have
        the same inventory view before deciding what to serve/download."""
        _scope_owner(request, COOKBOOK_READ_SCOPES)
        # Hit /api/model/cached internally, with the same modelDirs the chat
        # agent's list_cached_models would resolve from cookbook state.
        state = _read_cookbook_state()
        env = state.get("env") if isinstance(state, dict) else {}
        servers = (env.get("servers") if isinstance(env, dict) else None) or []
        HF_DEFAULTS = {"~/.cache/huggingface/hub", "~/.cache/huggingface"}
        def _dirs_for(srv: dict) -> str:
            mds = srv.get("modelDirs") if isinstance(srv, dict) else None
            if isinstance(mds, list):
                extras = [d for d in mds if isinstance(d, str) and d.strip() and d.strip() not in HF_DEFAULTS]
                return ",".join(extras)
            if isinstance(mds, str) and mds.strip() not in HF_DEFAULTS:
                return mds
            return ""
        # Resolve friendly host name → real host (matches list_cached_models flow).
        resolved_host = host or ""
        srv: dict[str, Any] = {}
        if host:
            srv = next(
                (s for s in servers if isinstance(s, dict)
                 and (s.get("name") == host or s.get("host") == host)),
                {},
            )
            if srv and srv.get("host"):
                resolved_host = srv["host"]
        else:
            srv = next((s for s in servers if isinstance(s, dict) and not (s.get("host") or "").strip()), {})
        params: dict[str, str] = {}
        if resolved_host:
            params["host"] = resolved_host
        md = _dirs_for(srv)
        if md:
            params["model_dir"] = md
        if srv.get("port"):
            params["ssh_port"] = str(srv["port"])
        if srv.get("platform"):
            params["platform"] = srv["platform"]
        cached_endpoint = _find_endpoint(None, "GET", "/api/model/cached")
        if cached_endpoint is None:
            from fastapi import FastAPI
            app: FastAPI = request.app
            for route in app.routes:
                if getattr(route, "path", None) == "/api/model/cached" and "GET" in getattr(route, "methods", set()):
                    cached_endpoint = route.endpoint
                    break
        if cached_endpoint is None:
            raise HTTPException(503, "model cached endpoint unavailable")
        # The endpoint reads host/model_dir/ssh_port/platform as kwargs.
        return await cached_endpoint(
            request,
            host=params.get("host") or None,
            model_dir=params.get("model_dir") or None,
            ssh_port=params.get("ssh_port") or None,
            platform=params.get("platform") or None,
        )

    @router.get("/cookbook/presets")
    async def codex_cookbook_presets(request: Request):
        """List saved serve presets (model + host + port + launch cmd).
        Counterpart to `list_serve_presets`. Use BEFORE composing a `serve`
        body — the user's saved preset usually has the working cmd already."""
        _scope_owner(request, COOKBOOK_READ_SCOPES)
        state = _read_cookbook_state()
        presets = state.get("presets") or []
        out = []
        for p in presets:
            if not isinstance(p, dict):
                continue
            out.append({
                "name": p.get("name"),
                "model": p.get("model") or p.get("modelId"),
                "host": p.get("host") or p.get("remoteHost"),
                "port": p.get("port"),
                "cmd": p.get("cmd"),
            })
        return {"presets": out, "default_host": (state.get("env") or {}).get("defaultServer", "")}

    @router.post("/cookbook/preset/{name}")
    async def codex_cookbook_serve_preset(request: Request, name: str):
        """Launch a saved preset by name. Reuses the working cmd + host the
        user already saved, avoiding the cmd-allowlist trial-and-error loop."""
        _scope_owner(request, COOKBOOK_LAUNCH_SCOPES)
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9 _.:@\-]+", name):
            raise HTTPException(400, "Invalid preset name")
        state = _read_cookbook_state()
        presets = state.get("presets") or []
        lname = name.lower().strip()
        chosen = next(
            (p for p in presets if isinstance(p, dict) and (p.get("name") or "").lower() == lname),
            None,
        )
        if chosen is None:
            chosen = next(
                (p for p in presets if isinstance(p, dict) and lname in (p.get("name") or "").lower()),
                None,
            )
        if chosen is None:
            raise HTTPException(404, f"No preset matching {name!r}")
        repo_id = chosen.get("model") or chosen.get("modelId") or ""
        cmd = (chosen.get("cmd") or "").strip()
        host = chosen.get("host") or chosen.get("remoteHost") or ""
        if not repo_id or not cmd or cmd.startswith("(adopted"):
            raise HTTPException(400, f"Preset {chosen.get('name')!r} has no launchable cmd "
                                     "(adopted from external launch). Use POST /cookbook/serve "
                                     "with the actual cmd instead.")
        # Reuse the serve handler we already validated.
        from routes.cookbook_helpers import ServeRequest
        body = {"repo_id": repo_id, "cmd": cmd}
        if host:
            body["remote_host"] = host
        try:
            req = ServeRequest(**body)
        except Exception as exc:
            raise HTTPException(400, f"Preset payload invalid: {exc}")
        serve_endpoint = _find_endpoint(None, "POST", "/api/model/serve")
        if serve_endpoint is None:
            from fastapi import FastAPI
            app: FastAPI = request.app
            for route in app.routes:
                if getattr(route, "path", None) == "/api/model/serve" and "POST" in getattr(route, "methods", set()):
                    serve_endpoint = route.endpoint
                    break
        if serve_endpoint is None:
            raise HTTPException(503, "model serve endpoint unavailable")
        return await serve_endpoint(request, req)

    @router.post("/cookbook/adopt")
    async def codex_cookbook_adopt(request: Request, body: dict[str, Any] = Body(default_factory=dict)):
        """Adopt an existing tmux session (one started via raw ssh+tmux) into
        cookbook tracking. Needed when serve_model rejects a cmd and the
        agent falls back to direct ssh — without adoption the session is
        invisible to the UI. Body: {tmux_session, model, host?, port?}."""
        _scope_owner(request, COOKBOOK_LAUNCH_SCOPES)
        norm = dict(body or {})
        sess = (norm.get("tmux_session") or norm.get("session_id") or "").strip()
        model = (norm.get("model") or norm.get("repo_id") or "").strip()
        host = (norm.get("host") or norm.get("remote_host") or "").strip()
        port = norm.get("port") or 8000
        import re as _re
        if not sess or not _re.fullmatch(r"[a-zA-Z0-9_-]+", sess):
            raise HTTPException(400, "tmux_session required, [a-zA-Z0-9_-]+ only")
        if not model:
            raise HTTPException(400, "model required")
        # Verify the tmux session exists on the target host before adopting.
        import shlex
        if host:
            check = f"ssh {shlex.quote(host)} 'tmux has-session -t {shlex.quote(sess)}'"
        else:
            check = f"tmux has-session -t {shlex.quote(sess)}"
        chk = await _run_shell(check, timeout=8)
        if chk.get("exit_code") not in (0, None):
            raise HTTPException(404, f"tmux session {sess!r} not found on {host or 'local'}")
        # Write into cookbook_state.json.
        import time as _t, json as _json
        from core.atomic_io import atomic_write_json
        from pathlib import Path as _Path
        cookbook_state_path = _Path(COOKBOOK_STATE_FILE)
        try:
            state = _json.loads(cookbook_state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        tasks = state.setdefault("tasks", [])
        if any(isinstance(t, dict) and t.get("sessionId") == sess for t in tasks):
            return {"ok": True, "already_tracked": True, "session_id": sess}
        tasks.append({
            "id": sess, "sessionId": sess,
            "name": model.split("/")[-1] if "/" in model else model,
            "type": "serve", "status": "running",
            "output": f"Adopted externally-launched session {sess!r} on {host or 'local'}.",
            "ts": int(_t.time() * 1000),
            "payload": {"repo_id": model, "remote_host": host, "_cmd": "(adopted — launched outside cookbook)", "port": int(port)},
            "remoteHost": host, "sshPort": "", "platform": "linux",
            "_serveReady": False, "_endpointAdded": False, "_adoptedExternally": True,
        })
        try:
            atomic_write_json(cookbook_state_path, state)
        except Exception as exc:
            raise HTTPException(500, f"state write failed: {exc}")
        return {"ok": True, "session_id": sess, "host": host or "local"}

    return router


def setup_claude_routes() -> APIRouter:
    """Serve the Claude Code skill bundle.

    Claude Code uses the same scope-gated `/api/codex/*` endpoints at runtime;
    this router only exists to deliver the skill zip via `/api/claude/plugin.zip`
    so the user-facing setup commands stay in the Claude namespace.
    """
    router = APIRouter(prefix="/api/claude", tags=["claude"])

    @router.get("/plugin.zip")
    def plugin_zip(request: Request):
        require_authenticated_request(request)
        # Only ship the skills/ subtree so extracting at ~/.claude/ doesn't dump
        # README.md or other bundle metadata into the user's claude config dir.
        skills_root = Path(__file__).resolve().parent.parent / "integrations" / "claude" / "skills"
        if not skills_root.exists():
            raise HTTPException(404, "Claude skill bundle not found")
        bundle_root = skills_root.parent
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(skills_root.rglob("*")):
                if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                zf.write(path, path.relative_to(bundle_root))
        buf.seek(0)
        headers = {"Content-Disposition": 'attachment; filename="odysseus-claude-skill.zip"'}
        return StreamingResponse(buf, media_type="application/zip", headers=headers)

    return router
