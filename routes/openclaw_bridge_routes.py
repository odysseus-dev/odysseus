"""OpenClaw bridge routes for Slack-first external ingress."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import SessionLocal, ScheduledTask, TaskRun, update_session_last_accessed
from core.models import ChatMessage
from routes.chat_helpers import (
    build_chat_context,
    clean_thinking_for_save,
    run_post_response_tasks,
    _enforce_chat_privileges,
)
from routes.chat_routes import _clear_orphaned_session_endpoint, _recover_empty_session_model
from src.auth_helpers import require_user
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import llm_call_async
from src.tool_policy import build_effective_tool_policy


CHAT_SCOPES = {"chat"}
CONVERGE_READ_SCOPES = {"converge:read"}
WORKFLOW_TRIGGER_SCOPES = {"workflows:trigger"}
WEB_READ_SCOPES = {"web:read"}
RESEARCH_RUN_SCOPES = {"research:run"}
TOOLS_USE_SCOPES = {"tools:use"}
MEMORY_WRITE_SCOPES = {"memory:write"}

logger = logging.getLogger(__name__)


def _scope_owner(request: Request, allowed: set[str]) -> str:
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


def _has_scope(request: Request, allowed: set[str]) -> bool:
    if not getattr(request.state, "api_token", False):
        return True
    scopes = set(getattr(request.state, "api_token_scopes", []) or [])
    return bool(scopes.intersection(allowed))


def _session_part(value: str | None, fallback: str) -> str:
    raw = (value or fallback).strip()[:96]
    safe = re.sub(r"[^A-Za-z0-9_.:@-]+", "-", raw).strip("-")
    return safe or fallback


def openclaw_session_id(channel: str | None = None, thread: str | None = None, session_id: str | None = None) -> str:
    if session_id:
        return _session_part(session_id, "default")
    return "openclaw:slack:%s:%s" % (
        _session_part(channel, "unknown-channel"),
        _session_part(thread, "root"),
    )


def _converge_config() -> tuple[str, str]:
    base_url = (os.getenv("CONVERGE_BASE_URL") or os.getenv("REDMINE_DASHBOARD_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("CONVERGE_API_KEY") or os.getenv("CONVERGE_EXTERNAL_API_KEY") or "").strip()
    missing = []
    if not base_url:
        missing.append("CONVERGE_BASE_URL")
    if not api_key:
        missing.append("CONVERGE_API_KEY")
    if missing:
        missing_text = ", ".join(missing)
        raise HTTPException(503, "Converge bridge not configured: missing " + missing_text)
    return base_url, api_key


def _allowed_workflows() -> set[str] | None:
    raw = (os.getenv("OPENCLAW_ALLOWED_WORKFLOWS") or "").strip()
    if not raw:
        return None
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


def _workflow_allowed(name: str, task_id: str | None = None, task_name: str | None = None) -> bool:
    allowed = _allowed_workflows()
    if allowed is None:
        return True
    if "*" in allowed:
        return True
    candidates = {name}
    if task_id:
        candidates.add(task_id)
    if task_name:
        candidates.add(task_name)
    return bool(candidates.intersection(allowed))


def _sanitize_ticket_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        allowed = {
            "id", "redmine_id", "redmineId", "subject", "title", "description",
            "status", "status_name", "priority", "project", "project_name",
            "tracker", "author", "assignee", "assigned_to", "created_on",
            "updated_on", "closed_on", "due_date", "start_date", "done_ratio",
            "estimated_hours", "spent_hours", "journals", "notes", "comments",
            "changes", "relations", "children", "parent", "custom_fields",
            "url", "external_url",
        }
        redacted_key_parts = ("token", "secret", "password", "authorization", "api_key", "apikey", "cookie")
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            key_lc = key_text.lower()
            if any(part in key_lc for part in redacted_key_parts):
                continue
            if depth == 0 and key_text not in allowed:
                continue
            sanitized[key_text] = _sanitize_ticket_payload(item, depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_ticket_payload(item, depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def _ensure_session(session_manager, session_id: str, owner: str, title: str):
    try:
        return session_manager.get_session(session_id)
    except KeyError:
        pass

    url, model, headers = resolve_endpoint("default", owner=owner)
    if not url or not model:
        url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise HTTPException(503, "No default or utility model endpoint configured for Odysseus")

    sess = session_manager.create_session(
        session_id=session_id,
        name=title[:160] or "OpenClaw Slack",
        endpoint_url=url,
        model=model,
        rag=False,
        owner=owner,
    )
    sess.headers = headers or {}
    return sess


class OpenClawAskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)
    channel: str | None = None
    thread: str | None = None
    session_id: str | None = None
    use_web: bool = False
    use_research: bool = False
    time_filter: str | None = None
    preset_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowTriggerRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class TicketSearchRequest(BaseModel):
    query: str | None = None
    redmine_id: int | None = None
    status: str | None = None
    project: str | None = None
    assignee: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


def setup_openclaw_bridge_routes(
    session_manager,
    chat_handler,
    chat_processor,
    memory_manager,
    research_handler,
    memory_vector=None,
    webhook_manager=None,
    task_scheduler=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

    @router.get("/health")
    async def health(request: Request):
        owner = _scope_owner(request, CHAT_SCOPES)
        result: dict[str, Any] = {
            "status": "ok",
            "message": "OpenClaw bridge reachable",
            "owner": owner,
            "odysseus": {"ok": True},
            "task_runner": {"configured": task_scheduler is not None},
        }
        if task_scheduler is not None:
            result["task_runner"]["running"] = bool(getattr(task_scheduler, "_running", False))
        return result

    @router.get("/converge/health")
    async def converge_health(request: Request):
        _scope_owner(request, CONVERGE_READ_SCOPES)
        result: dict[str, Any] = {"status": "ok", "converge": {"configured": False, "ok": False}}
        try:
            base_url, api_key = _converge_config()
            result["converge"]["configured"] = True
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base_url}/api/health")
                result["converge"]["health_status"] = resp.status_code
                result["converge"]["ok"] = resp.status_code < 500
                smoke = await client.get(
                    f"{base_url}/api/external/tickets",
                    headers={"X-API-Key": api_key},
                    params={"limit": 1},
                )
                result["converge"]["ticket_smoke_status"] = smoke.status_code
                result["converge"]["ticket_smoke_ok"] = smoke.status_code < 400
        except HTTPException:
            result["converge"]["message"] = "not configured"
        except Exception as exc:
            result["converge"]["message"] = str(exc)
        return result

    @router.post("/ask")
    async def ask(request: Request, body: OpenClawAskRequest):
        owner = _scope_owner(request, CHAT_SCOPES)
        web_allowed = _has_scope(request, WEB_READ_SCOPES)
        research_allowed = _has_scope(request, RESEARCH_RUN_SCOPES)
        tools_allowed = _has_scope(request, TOOLS_USE_SCOPES)
        memory_read_allowed = _has_scope(request, {"memory:read"})
        memory_write_allowed = _has_scope(request, MEMORY_WRITE_SCOPES)
        
        from routes.homelab_routes import HOMELAB_WRITE_SCOPES
        from routes.n8n_routes import N8N_WRITE_SCOPES
        homelab_write_allowed = _has_scope(request, HOMELAB_WRITE_SCOPES)
        n8n_write_allowed = _has_scope(request, N8N_WRITE_SCOPES)
        
        session_id = openclaw_session_id(body.channel, body.thread, body.session_id)
        sess = _ensure_session(session_manager, session_id, owner, f"OpenClaw Slack {body.channel or ''}".strip())

        if _clear_orphaned_session_endpoint(sess, owner=owner):
            raise HTTPException(400, "Selected model endpoint was removed. Pick another model in Settings.")
        _recover_empty_session_model(sess, session_id, owner=owner)
        if not getattr(sess, "model", "").strip():
            raise HTTPException(400, "No model selected for this bridge session.")

        original_user = getattr(request.state, "current_user", None)
        original_api_token = getattr(request.state, "api_token", None)
        request.state.current_user = owner
        request.state.api_token = False
        try:
            _enforce_chat_privileges(request, sess)
            tool_policy = build_effective_tool_policy(last_user_message=body.message)
            warnings: list[str] = []
            if body.use_web and not web_allowed:
                raise HTTPException(403, "API token missing required scope: web:read")
            if body.use_research and not research_allowed:
                raise HTTPException(403, "API token missing required scope: research:run")
            allow_tool_preprocessing = not tool_policy.block_all_tool_calls and tools_allowed

            memory_response = None
            if not tool_policy.blocks("manage_memory") and memory_write_allowed:
                memory_response = await chat_handler.handle_memory_command(sess, body.message)
            if memory_response:
                return {
                    "status": "ok",
                    "message": memory_response,
                    "session_id": session_id,
                    "task_id": None,
                    "run_id": None,
                    "links": [],
                    "warnings": warnings,
                    "requires_approval": False,
                }

            ctx = await build_chat_context(
                sess,
                request,
                chat_handler,
                chat_processor,
                message=body.message,
                session_id=session_id,
                preset_id=body.preset_id,
                att_ids=[],
                use_web=body.use_web,
                time_filter=body.time_filter,
                no_memory=not memory_read_allowed,
                webhook_manager=webhook_manager,
                allow_tool_preprocessing=allow_tool_preprocessing,
            )
            
            gate_msg = ""
            if homelab_write_allowed or n8n_write_allowed:
                gate_msg = "You have permissions for write-heavy homelab or n8n actions. You MUST ask the user for explicit confirmation before generating tool calls or recommending commands that modify state (e.g., restart containers, rerun workflows)."
            else:
                gate_msg = "You do NOT have permissions for write-heavy actions like restarting containers or rerunning workflows. Do not attempt to use or recommend them."
            
            ctx.messages.insert(len(ctx.preface), {"role": "system", "content": gate_msg})

            if body.use_research and not tool_policy.blocks("trigger_research"):
                try:
                    from routes.research_routes import _resolve_research_endpoint
                    from src.prompt_security import untrusted_context_message
                    _r_ep, _r_model, _r_headers = _resolve_research_endpoint(sess)
                    research_ctx = await research_handler.call_research_service(
                        body.message, _r_ep, _r_model, llm_headers=_r_headers
                    )
                    ctx.messages.insert(
                        len(ctx.preface),
                        untrusted_context_message("research context", research_ctx),
                    )
                except Exception as exc:
                    logger.warning("OpenClaw bridge research failed: %s", exc, exc_info=True)
                    warnings.append("Research context unavailable: " + str(exc)[:200])

            reply = await llm_call_async(
                sess.endpoint_url,
                sess.model,
                ctx.messages,
                headers=sess.headers,
                temperature=ctx.preset.temperature,
                max_tokens=ctx.preset.max_tokens,
                prompt_type=body.preset_id,
                session_id=session_id,
            )
            clean_reply, clean_md = clean_thinking_for_save(reply, {"model": sess.model})
            sess.add_message(ChatMessage("assistant", clean_reply, metadata=clean_md))
            update_session_last_accessed(session_id)
            session_manager.save_sessions()
            run_post_response_tasks(
                sess,
                session_manager,
                session_id,
                body.message,
                reply,
                None,
                ctx.uprefs,
                memory_manager,
                memory_vector,
                webhook_manager,
                character_name=ctx.preset.character_name,
                owner=ctx.user,
                allow_background_extraction=not tool_policy.block_all_tool_calls,
            )
            return {
                "status": "ok",
                "message": reply,
                "session_id": session_id,
                "task_id": None,
                "run_id": None,
                "links": [],
                "warnings": warnings,
                "requires_approval": False,
            }
        finally:
            session_manager.save_sessions()
            request.state.current_user = original_user
            if original_api_token is None:
                try:
                    delattr(request.state, "api_token")
                except AttributeError:
                    pass
            else:
                request.state.api_token = original_api_token

    @router.post("/tickets/search")
    async def ticket_search(request: Request, body: TicketSearchRequest):
        _scope_owner(request, CONVERGE_READ_SCOPES)
        base_url, api_key = _converge_config()
        params: dict[str, Any] = {"limit": body.limit, "offset": body.offset}
        if body.query:
            params["search"] = body.query
        if body.redmine_id is not None:
            params["redmineId"] = str(body.redmine_id)
        for key in ("status", "project", "assignee"):
            value = getattr(body, key)
            if value:
                params[key] = value
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{base_url}/api/external/tickets",
                headers={"X-API-Key": api_key},
                params=params,
            )
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text[:500])
        data = resp.json()
        return {
            "status": "ok",
            "message": f"Found {len(data.get('tickets', []))} ticket(s)",
            "session_id": None,
            "task_id": None,
            "run_id": None,
            "links": [],
            "tickets": data.get("tickets", []),
            "total": data.get("total"),
            "requires_approval": False,
        }

    @router.post("/tickets/{ticket_id}/summary")
    async def ticket_summary(request: Request, ticket_id: str):
        owner = _scope_owner(request, CONVERGE_READ_SCOPES)
        base_url, api_key = _converge_config()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{base_url}/api/external/tickets/{ticket_id}",
                headers={"X-API-Key": api_key},
            )
        if resp.status_code >= 400:
            raise HTTPException(resp.status_code, resp.text[:500])
        ticket = resp.json()
        sanitized_ticket = _sanitize_ticket_payload(ticket)
        prompt = (
            "Summarize this Redmine ticket for a Slack operations thread. "
            "Include status, owner, project, what is being asked, recent risk, and next action.\n\n"
            f"{json.dumps(sanitized_ticket, ensure_ascii=False)}"
        )
        session_id = openclaw_session_id("converge", f"ticket-{ticket_id}")
        sess = _ensure_session(session_manager, session_id, owner, f"Converge ticket {ticket_id}")
        original_user = getattr(request.state, "current_user", None)
        original_api_token = getattr(request.state, "api_token", None)
        request.state.current_user = owner
        request.state.api_token = False
        try:
            ctx = await build_chat_context(
                sess,
                request,
                chat_handler,
                chat_processor,
                message=prompt,
                session_id=session_id,
                att_ids=[],
                use_web=False,
                webhook_manager=webhook_manager,
            )
            reply = await llm_call_async(
                sess.endpoint_url,
                sess.model,
                ctx.messages,
                headers=sess.headers,
                temperature=ctx.preset.temperature,
                max_tokens=ctx.preset.max_tokens,
                session_id=session_id,
            )
            clean_reply, clean_md = clean_thinking_for_save(reply, {"model": sess.model})
            sess.add_message(ChatMessage("assistant", clean_reply, metadata=clean_md))
            update_session_last_accessed(session_id)
            return {
                "status": "ok",
                "message": reply,
                "session_id": session_id,
                "task_id": None,
                "run_id": None,
                "links": [],
                "ticket": ticket,
                "ticket_for_summary": sanitized_ticket,
                "requires_approval": False,
            }
        finally:
            request.state.current_user = original_user
            if original_api_token is None:
                try:
                    delattr(request.state, "api_token")
                except AttributeError:
                    pass
            else:
                request.state.api_token = original_api_token

    @router.post("/workflows/{name}/trigger")
    async def workflow_trigger(request: Request, name: str, body: WorkflowTriggerRequest):
        owner = _scope_owner(request, WORKFLOW_TRIGGER_SCOPES)
        if task_scheduler is None:
            raise HTTPException(503, "Task scheduler not configured")
        db = SessionLocal()
        try:
            task = (
                db.query(ScheduledTask)
                .filter(ScheduledTask.owner == owner)
                .filter((ScheduledTask.id == name) | (ScheduledTask.name == name))
                .first()
            )
            if not task:
                raise HTTPException(404, "Workflow not found")
            if not _workflow_allowed(name, task_id=task.id, task_name=task.name):
                raise HTTPException(403, "Workflow is not allowed for OpenClaw bridge")
            task_id = task.id
        finally:
            db.close()
        started = await task_scheduler.run_task_now(task_id, force=body.force)
        if not started:
            raise HTTPException(409, "Workflow is already running")
        run_id = None
        db = SessionLocal()
        try:
            run = (
                db.query(TaskRun)
                .filter(TaskRun.task_id == task_id)
                .order_by(TaskRun.started_at.desc())
                .first()
            )
            run_id = run.id if run else None
        finally:
            db.close()
        return {
            "status": "queued",
            "message": "Workflow triggered",
            "session_id": None,
            "task_id": task_id,
            "run_id": run_id,
            "links": [],
            "requires_approval": False,
        }

    return router
