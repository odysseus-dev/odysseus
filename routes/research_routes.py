"""Research background task routes — /api/research/*."""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from src.endpoint_resolver import resolve_endpoint
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

# Model-name substrings that are NOT chat/generation models — research must
# never pick these as its model. An OpenAI-style endpoint often lists
# `text-embedding-ada-002` etc. first in its model list, which is why research
# was failing with "Cannot reach model 'text-embedding-ada-002'".
_NON_CHAT_MODEL = (
    "text-embedding", "embedding", "tts-", "whisper", "dall-e",
    "moderation", "rerank", "reranker", "clip", "stable-diffusion",
)


def _first_chat_model(models) -> str:
    """First model that isn't an embedding/tts/etc. — falls back to models[0]."""
    for m in (models or []):
        if not any(p in str(m).lower() for p in _NON_CHAT_MODEL):
            return m
    return (models[0] if models else "")


def _resolve_research_endpoint(sess) -> tuple:
    """Return (endpoint_url, model, headers) for Deep Research, checking admin overrides."""
    url, model, headers = resolve_endpoint(
        "research",
        fallback_url=sess.endpoint_url,
        fallback_model=sess.model,
        fallback_headers=sess.headers,
    )
    return url, model, headers


def setup_research_routes(research_handler, session_manager=None) -> APIRouter:
    router = APIRouter(tags=["research"])

    def _require_user(request: Request) -> str:
        """All research endpoints require an authenticated user. Research
        data isn't owner-scoped in the on-disk JSON yet, so we at least
        block anonymous access. Multi-tenant deploys should additionally
        verify the session belongs to this user."""
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        return user

    def _owns_in_memory(session_id: str, user: str) -> bool:
        """Ownership check for an in-flight (in-memory) research task.
        Falls back to the on-disk JSON if the task has already finished."""
        entry = research_handler._active_tasks.get(session_id)
        if entry is not None:
            return entry.get("owner", "") == user
        # Task no longer in memory — check the persisted JSON.
        path = Path("data/deep_research") / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("owner") == user
        except Exception:
            return False

    @router.get("/api/research/active")
    async def research_active(request: Request):
        """List all currently active (running) research tasks."""
        user = _require_user(request)
        active = []
        for sid, entry in research_handler._active_tasks.items():
            # SECURITY: only show this user's running tasks.
            if entry.get("owner", "") != user:
                continue
            if entry.get("status") == "running":
                active.append({
                    "session_id": sid,
                    "query": entry.get("query", ""),
                    "status": "running",
                    "progress": entry.get("progress", {}),
                    "started_at": entry.get("started_at", 0),
                })
        return {"active": active}

    @router.get("/api/research/status/{session_id}")
    async def research_status(session_id: str, request: Request):
        user = _require_user(request)
        if not _owns_in_memory(session_id, user):
            raise HTTPException(404, "No research found for this session")
        status = research_handler.get_status(session_id)
        if status is None:
            raise HTTPException(404, "No research found for this session")
        return status

    @router.post("/api/research/cancel/{session_id}")
    async def research_cancel(session_id: str, request: Request):
        user = _require_user(request)
        if not _owns_in_memory(session_id, user):
            raise HTTPException(404, "No research found for this session")
        cancelled = research_handler.cancel_research(session_id)
        return {"cancelled": cancelled}

    @router.post("/api/research/result/{session_id}")
    async def research_result(session_id: str, request: Request):
        user = _require_user(request)
        if not _owns_in_memory(session_id, user):
            raise HTTPException(404, "No research result available")
        result = research_handler.get_result(session_id)
        if result is None:
            raise HTTPException(404, "No research result available")
        sources = research_handler.get_sources(session_id) or []
        raw_findings = research_handler.get_raw_findings(session_id) or []
        research_handler.clear_result(session_id)
        return {"result": result, "sources": sources, "raw_findings": raw_findings}

    def _assert_owns_research(session_id: str, user: str) -> None:
        """404-not-403 ownership gate for a research session's on-disk JSON.
        Use BEFORE returning any data or mutating the file."""
        path = Path("data/deep_research") / f"{session_id}.json"
        if not path.exists():
            raise HTTPException(404, "Research not found")
        try:
            owner = json.loads(path.read_text(encoding="utf-8")).get("owner")
        except Exception:
            raise HTTPException(404, "Research not found")
        if owner != user:
            raise HTTPException(404, "Research not found")

    @router.get("/api/research/report/{session_id}")
    async def research_report(session_id: str, request: Request):
        """Serve the visual HTML report for a completed research session."""
        user = _require_user(request)
        _assert_owns_research(session_id, user)
        logger.info(f"Visual report requested for session {session_id}")
        try:
            html_content = research_handler.get_report_html(session_id)
        except Exception as e:
            logger.error(f"Visual report generation error: {e}", exc_info=True)
            raise HTTPException(500, f"Report generation failed: {e}")
        if html_content is None:
            logger.warning(f"No report data found for session {session_id}")
            raise HTTPException(404, "No visual report available for this session")
        return HTMLResponse(content=html_content)

    class HideImageRequest(BaseModel):
        url: str

    @router.post("/api/research/{session_id}/hide-image")
    async def research_hide_image(session_id: str, body: HideImageRequest, request: Request):
        """Mark an image URL as hidden for this research's visual report.
        Persisted to the research JSON so subsequent /report renders skip it."""
        user = _require_user(request)
        _assert_owns_research(session_id, user)
        ok = research_handler.hide_image(session_id, body.url)
        if not ok:
            raise HTTPException(404, "Research not found")
        return {"ok": True}

    @router.post("/api/research/{session_id}/unhide-images")
    async def research_unhide_images(session_id: str, request: Request):
        """Clear the hidden-images list for a research session."""
        user = _require_user(request)
        _assert_owns_research(session_id, user)
        ok = research_handler.unhide_all_images(session_id)
        if not ok:
            raise HTTPException(404, "Research not found")
        return {"ok": True}

    @router.get("/api/research/library")
    async def research_library(
        request: Request,
        search: Optional[str] = Query(None),
        sort: str = Query("recent"),
        limit: int = Query(50),
        archived: bool = Query(False),
    ):
        user = _require_user(request)
        """List all completed research for the Library panel."""
        data_dir = Path("data/deep_research")
        items = []
        for p in data_dir.glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                # SECURITY: only show research belonging to this user. Legacy
                # JSONs without an `owner` field are hidden — auth was the only
                # gate before, so every user saw every other user's reports.
                if d.get("owner") != user:
                    continue
                # Archived view shows ONLY archived reports; default hides them.
                if bool(d.get("archived")) != archived:
                    continue
                query = d.get("query", "")
                if search and search.lower() not in query.lower():
                    continue
                sources = d.get("sources", [])
                items.append({
                    "id": p.stem,
                    "query": query,
                    "category": d.get("category") or "",
                    "source_count": len(sources),
                    "status": d.get("status", "done"),
                    "duration": d.get("stats", {}).get("Duration", ""),
                    "rounds": d.get("stats", {}).get("Rounds", ""),
                    "started_at": d.get("started_at", 0),
                    "completed_at": d.get("completed_at", 0),
                    "archived": bool(d.get("archived")),
                })
            except Exception:
                continue

        # Sort
        if sort == "recent":
            items.sort(key=lambda x: x["completed_at"] or 0, reverse=True)
        elif sort == "oldest":
            items.sort(key=lambda x: x["completed_at"] or 0)
        elif sort == "most-messages":
            items.sort(key=lambda x: x["source_count"], reverse=True)
        elif sort == "alpha":
            items.sort(key=lambda x: x["query"].lower())

        return {"research": items[:limit], "total": len(items)}

    @router.get("/api/research/detail/{session_id}")
    async def research_detail(session_id: str, request: Request):
        """Return the full JSON for a single research result — sources,
        summary, stats — used by the Library preview panel."""
        user = _require_user(request)
        path = Path("data/deep_research") / f"{session_id}.json"
        if not path.exists():
            raise HTTPException(404, "Research not found")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(500, f"Failed to read research: {e}")
        # SECURITY: 404 (not 403) so we don't leak that the report exists.
        if data.get("owner") != user:
            raise HTTPException(404, "Research not found")
        return data

    @router.post("/api/research/{session_id}/archive")
    async def research_archive(session_id: str, request: Request, archived: bool = Query(True)):
        """Soft-archive / restore a research report (sets `archived` in its JSON)."""
        user = _require_user(request)
        path = Path("data/deep_research") / f"{session_id}.json"
        if not path.exists():
            raise HTTPException(404, "Research not found")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("owner") != user:
                raise HTTPException(404, "Research not found")
            data["archived"] = bool(archived)
            path.write_text(json.dumps(data), encoding="utf-8")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"Failed to update research: {e}")
        return {"ok": True, "id": session_id, "archived": bool(archived)}

    @router.delete("/api/research/{session_id}")
    async def research_delete(session_id: str, request: Request):
        """Delete a research result from disk."""
        user = _require_user(request)
        data_dir = Path("data/deep_research")
        json_path = data_dir / f"{session_id}.json"
        deleted = False
        if json_path.exists():
            # SECURITY: verify ownership before letting the caller delete it.
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                if data.get("owner") != user:
                    raise HTTPException(404, "Research not found")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(404, "Research not found")
            json_path.unlink()
            deleted = True
        return {"deleted": deleted}

    # ------------------------------------------------------------------
    # Panel endpoints — launch research without a chat session
    # ------------------------------------------------------------------

    class ResearchStartRequest(BaseModel):
        query: str
        # max_rounds=0 means "Auto" — let the AI decide when to stop, capped at 20.
        max_rounds: int = Field(default=0, ge=0, le=20)
        search_provider: Optional[str] = None
        endpoint_id: Optional[str] = None
        model: Optional[str] = None
        max_time: int = Field(default=300, ge=60, le=1800)
        extraction_timeout: Optional[int] = Field(default=None, ge=15, le=600)
        extraction_concurrency: Optional[int] = Field(default=None, ge=1, le=12)
        category: Optional[str] = None

    @router.post("/api/research/start")
    async def research_start(body: ResearchStartRequest, request: Request):
        """Launch a research job from the dedicated panel."""
        from src.auth_helpers import require_privilege
        user = require_privilege(request, "can_use_research")
        if user == "internal-tool":
            tool_owner = (request.headers.get("X-Odysseus-Owner") or "").strip()
            if tool_owner and tool_owner not in {"internal-tool", "api", "demo", "system"}:
                auth_mgr = getattr(request.app.state, "auth_manager", None)
                if auth_mgr is not None and getattr(auth_mgr, "is_configured", False):
                    try:
                        privs = auth_mgr.get_privileges(tool_owner) or {}
                        if not privs.get("can_use_research", True):
                            raise HTTPException(403, f"Your account is not allowed to can use research.")
                    except HTTPException:
                        raise
                    except Exception:
                        pass
                user = tool_owner
        session_id = f"rp-{uuid.uuid4().hex[:12]}"

        if body.endpoint_id:
            from src.database import SessionLocal
            from src.database import ModelEndpoint
            from src.endpoint_resolver import normalize_base, build_chat_url, build_headers
            db = SessionLocal()
            try:
                ep = db.query(ModelEndpoint).filter(
                    ModelEndpoint.id == body.endpoint_id,
                    ModelEndpoint.is_enabled == True,
                ).first()
                if not ep:
                    raise HTTPException(404, "Endpoint not found or disabled")
                base = normalize_base(ep.base_url)
                ep_url = build_chat_url(base)
                ep_headers = build_headers(ep.api_key, base)
                ep_model = body.model or ""
                if not ep_model:
                    try:
                        import json as _json
                        models = _json.loads(ep.cached_models) if ep.cached_models else []
                        if models:
                            ep_model = _first_chat_model(models)
                    except Exception:
                        pass
            finally:
                db.close()
        else:
            ep_url, ep_model, ep_headers = resolve_endpoint("research")
            if not ep_url:
                ep_url, ep_model, ep_headers = resolve_endpoint("utility")
            # When neither research nor utility is configured, use the user's
            # configured DEFAULT model (default_endpoint_id/default_model) rather
            # than arbitrarily grabbing the first enabled endpoint's first model
            # (which surfaced gpt-3.5). "Default" should mean the default model.
            if not ep_url:
                ep_url, ep_model, ep_headers = resolve_endpoint("default")
            if not ep_url:
                ep_url, ep_model, ep_headers = resolve_endpoint("chat")
            if not ep_url:
                from src.database import SessionLocal
                from src.database import ModelEndpoint
                from src.endpoint_resolver import normalize_base, build_chat_url, build_headers
                db = SessionLocal()
                try:
                    ep = db.query(ModelEndpoint).filter(
                        ModelEndpoint.is_enabled == True,
                    ).first()
                    if ep:
                        base = normalize_base(ep.base_url)
                        ep_url = build_chat_url(base)
                        ep_headers = build_headers(ep.api_key, base)
                        ep_model = ""
                        if ep.cached_models:
                            try:
                                import json as _json
                                models = _json.loads(ep.cached_models)
                                if models:
                                    ep_model = _first_chat_model(models)
                            except Exception:
                                pass
                finally:
                    db.close()
            if not ep_url:
                raise HTTPException(400, "No endpoints configured. Add one in Settings first.")
            if body.model:
                ep_model = body.model

        # max_rounds=0 → "Auto", let AI decide; pass 20 as the safety cap.
        effective_max_rounds = body.max_rounds if body.max_rounds > 0 else 20
        research_handler.start_research(
            session_id=session_id,
            query=body.query,
            llm_endpoint=ep_url,
            llm_model=ep_model,
            max_time=body.max_time,
            llm_headers=ep_headers,
            max_rounds=effective_max_rounds,
            search_provider=body.search_provider or None,
            category=body.category or None,
            extraction_timeout=body.extraction_timeout,
            extraction_concurrency=body.extraction_concurrency,
            owner=user,
        )
        return {"session_id": session_id, "status": "running", "query": body.query}

    @router.get("/api/research/stream/{session_id}")
    async def research_stream(session_id: str, request: Request):
        """SSE stream of research progress events."""
        user = _require_user(request)
        if not _owns_in_memory(session_id, user):
            raise HTTPException(404, "No research found for this session")
        async def _generate():
            last_progress = None
            while True:
                status = research_handler.get_status(session_id)
                if status is None:
                    yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                    return
                st = status.get("status", "")
                progress = status.get("progress", {})
                if progress != last_progress:
                    last_progress = progress
                    yield f"data: {json.dumps({**progress, 'status': st})}\n\n"
                if st != "running":
                    final = {'status': st, 'final': True}
                    task = research_handler._active_tasks.get(session_id, {})
                    if st == "error" and task.get("result"):
                        final['error'] = str(task["result"])[:500]
                    yield f"data: {json.dumps(final)}\n\n"
                    return
                await asyncio.sleep(1.5)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/research/result-peek/{session_id}")
    async def research_result_peek(session_id: str, request: Request):
        """Get research result without clearing it (for panel use)."""
        user = _require_user(request)
        if not _owns_in_memory(session_id, user):
            raise HTTPException(404, "No research found for this session")
        result = research_handler.get_result(session_id)
        if result is None:
            p = Path("data/deep_research") / f"{session_id}.json"
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                return {
                    "result": d.get("result", ""),
                    "sources": d.get("sources", []),
                    "raw_findings": d.get("raw_findings", []),
                    "category": d.get("category") or "",
                }
            raise HTTPException(404, "No research result available")
        sources = research_handler.get_sources(session_id) or []
        raw_findings = research_handler.get_raw_findings(session_id) or []
        return {"result": result, "sources": sources, "raw_findings": raw_findings, "category": ""}

    @router.post("/api/research/spinoff/{session_id}")
    async def research_spinoff(session_id: str, request: Request):
        """Create a new chat session pre-seeded with this research as context.

        Reads the persisted research result + sources for `session_id`, creates
        a fresh session (inheriting endpoint/model/headers from the source
        session if available, otherwise from the resolved chat endpoint), and
        injects a single system message containing the report and sources so
        the user can ask follow-up questions in a clean conversation.
        """
        _require_user(request)
        if session_manager is None:
            raise HTTPException(500, "session_manager not configured")

        # Load research data — prefer in-memory result, fall back to disk
        result = research_handler.get_result(session_id)
        sources = research_handler.get_sources(session_id) or []
        query = ""

        path = Path("data/deep_research") / f"{session_id}.json"
        if path.exists():
            try:
                disk = json.loads(path.read_text(encoding="utf-8"))
                if not result:
                    result = disk.get("result")
                if not sources:
                    sources = disk.get("sources", []) or []
                query = disk.get("query", "") or ""
            except Exception as e:
                logger.warning(f"Could not read research JSON for spinoff: {e}")

        if not result:
            raise HTTPException(404, "No research result available for this session")

        # Inherit endpoint/model/headers from the source session when possible.
        # For panel-launched research (rp-* IDs), there is no chat session, so
        # fall back through the same chain as /api/research/start: research →
        # utility → first enabled endpoint in the DB.
        ep_url, ep_model, ep_headers = "", "", {}
        try:
            src_sess = session_manager.get_session(session_id)
            ep_url = src_sess.endpoint_url or ""
            ep_model = src_sess.model or ""
            ep_headers = dict(src_sess.headers or {})
        except KeyError:
            pass

        def _merge(r_url, r_model, r_headers):
            nonlocal ep_url, ep_model, ep_headers
            if not ep_url and r_url:
                ep_url = r_url
            if not ep_model and r_model:
                ep_model = r_model
            if not ep_headers and r_headers:
                ep_headers = dict(r_headers)

        if not ep_url or not ep_model:
            _merge(*resolve_endpoint("chat"))
        if not ep_url or not ep_model:
            _merge(*resolve_endpoint("research"))
        if not ep_url or not ep_model:
            _merge(*resolve_endpoint("utility"))
        if not ep_url or not ep_model:
            # Last resort: any enabled endpoint
            from src.database import SessionLocal
            from src.database import ModelEndpoint
            from src.endpoint_resolver import normalize_base, build_chat_url, build_headers
            db = SessionLocal()
            try:
                ep = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).first()
                if ep:
                    base = normalize_base(ep.base_url)
                    fallback_url = build_chat_url(base)
                    fallback_headers = build_headers(ep.api_key, base)
                    fallback_model = ""
                    if ep.cached_models:
                        try:
                            models = json.loads(ep.cached_models)
                            if models:
                                fallback_model = models[0]
                        except Exception:
                            pass
                    _merge(fallback_url, fallback_model, fallback_headers)
            finally:
                db.close()

        if not ep_url or not ep_model:
            raise HTTPException(400, "No endpoint configured — add one in Settings first")

        # Create new session
        new_sid = str(uuid.uuid4())
        user = get_current_user(request)

        title_query = (query or "research").strip()
        if len(title_query) > 60:
            title_query = title_query[:57] + "…"
        new_name = f"Follow-up: {title_query}"

        new_sess = session_manager.create_session(
            session_id=new_sid,
            name=new_name,
            endpoint_url=ep_url,
            model=ep_model,
            rag=False,
            owner=user,
        )
        if ep_headers:
            new_sess.headers = ep_headers
            session_manager.save_sessions()
        try:
            from src.event_bus import fire_event
            fire_event("session_created", user)
        except Exception:
            logger.debug("session_created event dispatch failed", exc_info=True)

        # Build the priming system message — report only, no sources injected.
        # The user can open the visual report for source details; keeping sources
        # out of the chat context saves tokens and avoids the AI fabricating
        # citations.
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        primer = (
            f"[Research context — {date_str}]\n\n"
            f"The user previously ran a deep research investigation. Use the "
            f"report below as your primary knowledge base when answering "
            f"follow-up questions. If the user asks something not covered, "
            f"say so plainly rather than guessing.\n\n"
            f"=== ORIGINAL QUERY ===\n{query or '(not recorded)'}\n\n"
            f"=== REPORT ===\n{result}"
        )

        from core.models import ChatMessage
        new_sess.add_message(ChatMessage(
            role="system",
            content=primer,
            metadata={"research_spinoff_from": session_id},
        ))
        session_manager.save_sessions()

        return {
            "session_id": new_sid,
            "name": new_name,
            "source_count": len(sources),
        }

    return router
