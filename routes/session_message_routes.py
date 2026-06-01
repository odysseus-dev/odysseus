# routes/session_message_routes.py
"""
Message and content routes for sessions — history, export, compact, inject.

Split from session_routes.py to keep each file under 500 lines.
"""
import re
from datetime import datetime
from fastapi import APIRouter, HTTPException, Response, Request
import logging

from core.session_manager import SessionManager
from core.models import ChatMessage
from core.database import SessionLocal
from src.auth_helpers import get_current_user
from routes.session_routes import _verify_session_owner, _pick_endpoint_for_sort

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["session-messages"])


def setup_session_message_routes(
    session_manager: SessionManager,
    config: dict,
    webhook_manager=None,
):
    """Setup message-related session routes with the provided manager and config."""

    SESSIONS_FILE = config.get("SESSIONS_FILE")

    @router.get("/history/{sid}")
    def get_history(request: Request, sid: str):
        _verify_session_owner(request, sid)
        try:
            session = session_manager.get_session(sid)
        except KeyError:
            raise HTTPException(404, f"Session {sid} not found")
        return {"history": [msg.to_dict() for msg in session.history]}

    @router.get("/session/{sid}/export")
    def export_session(
        request: Request, sid: str, fmt: str = "md", filename: str = ""
    ):
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
            html_parts = [
                "<!DOCTYPE html><html><head>",
                f"<meta charset='utf-8'><title>{session.name}</title>",
                "<style>body{font-family:monospace;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#ddd}",
                ".msg{margin:1rem 0;padding:0.8rem;border-radius:6px;border:1px solid #333}",
                ".user{background:#1a1a2e}.ai{background:#1a2e1a}",
                ".role{font-weight:bold;margin-bottom:0.4rem;opacity:0.7;text-transform:uppercase;font-size:0.85em}",
                "pre{background:#000;padding:0.5rem;border-radius:4px;overflow-x:auto}</style></head><body>",
                f"<h1>{session.name}</h1>",
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
        for m in messages:
            sess.add_message(ChatMessage(m["role"], m["content"], metadata=m.get("metadata")))
        session_manager.save_sessions()
        return {"ok": True, "count": len(messages)}

    @router.post("/sessions/save")
    def sessions_save_now(request: Request):
        user = get_current_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")
        session_manager.save_sessions()
        return {"ok": True, "path": SESSIONS_FILE}

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

        recent_keep = min(8, max(4, len(history) // 4))
        older = history[:-recent_keep]
        recent = history[-recent_keep:]
        if not older:
            raise HTTPException(400, "Nothing old enough to compact")

        from src.context_compactor import SELF_SUMMARY_SYSTEM_PROMPT
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async

        url, model, headers = resolve_endpoint("utility")
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
