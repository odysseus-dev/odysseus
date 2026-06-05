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

from src.auth_helpers import require_user
from src.tool_implementations import do_manage_notes


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


async def _as_owner(request: Request, owner: str, fn, *args, **kwargs):
    """Run an existing route handler with request.state.current_user temporarily
    set to ``owner`` so its internal get_current_user/require_user calls see
    the scope-gated owner (not the "api" pseudo-user the bearer middleware sets).
    Restores the original value when done. Works for sync and async handlers."""
    orig = getattr(request.state, "current_user", None)
    request.state.current_user = owner
    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    finally:
        request.state.current_user = orig


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
            },
            "safety": {
                "email_send_requires_confirmation": True,
                "destructive_actions_should_confirm": True,
            },
        }

    @router.get("/plugin.zip")
    def plugin_zip(request: Request):
        require_user(request)
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
        require_user(request)
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
