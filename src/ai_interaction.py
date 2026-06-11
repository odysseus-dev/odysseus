"""
ai_interaction.py

AI-to-AI interaction tools: chat_with_model, create_session, list_sessions,
send_to_session, pipeline.

These are agent tools — the LLM writes fenced code blocks and they execute
through the standard agent_tools.py pipeline.
"""

import json
import logging
import uuid
import time
from typing import Dict, Optional, Tuple

from src.constants import GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)

AI_CHAT_TIMEOUT = 120  # seconds for a single LLM call
MAX_DEBATE_ROUNDS = 5
MAX_PIPELINE_STEPS = 10

# ---------------------------------------------------------------------------
# Global managers (set from app.py, same pattern as _mcp_manager)
# _session_manager is kept as a local cache for performance (avoiding
# repeated get_session_manager_instance() calls). It's synced with
# the authoritative singleton in core.models.
_session_manager = None
_memory_manager = None
_memory_vector = None
_rag_manager = None
_personal_docs_manager = None


def set_session_manager(mgr):
    """Set the global session manager. Syncs local cache + core singleton."""
    global _session_manager
    _session_manager = mgr
    from core.models import set_session_manager_instance
    set_session_manager_instance(mgr)


def get_session_manager():
    """Get the global session manager."""
    return _session_manager


def set_memory_manager(mgr, vector=None):
    global _memory_manager, _memory_vector
    _memory_manager = mgr
    _memory_vector = vector


def set_rag_manager(rag_mgr, personal_docs_mgr=None):
    global _rag_manager, _personal_docs_manager
    _rag_manager = rag_mgr
    _personal_docs_manager = personal_docs_mgr


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

from src.endpoint_resolver import build_chat_url, build_headers, build_models_url, resolve_endpoint_runtime


def _resolve_model(spec: str, owner: Optional[str] = None) -> Tuple[str, str, Dict]:
    """Resolve a model specifier to (endpoint_url, model_id, headers).

    Accepts:
      "model_name"              — searches all configured endpoints
      "model_name@endpoint_name" — looks up specific endpoint by display name

    Raises ValueError if model not found.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    spec = spec.strip()
    target_endpoint_name = None

    if "@" in spec:
        model_name, target_endpoint_name = spec.rsplit("@", 1)
        model_name = model_name.strip()
        target_endpoint_name = target_endpoint_name.strip()
    else:
        model_name = spec

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if target_endpoint_name:
            query = query.filter(ModelEndpoint.name.ilike(f"%{target_endpoint_name}%"))
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()

        if not endpoints:
            raise ValueError("No enabled endpoints found" +
                             (f" matching '{target_endpoint_name}'" if target_endpoint_name else ""))

        for ep in endpoints:
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

            if provider == "anthropic":
                # Anthropic: match against hardcoded model list
                matched = None
                for am in ANTHROPIC_MODELS:
                    if model_name.lower() in am.lower() or am.lower() in model_name.lower():
                        matched = am
                        break
                if matched:
                    return build_chat_url(base), matched, headers
            else:
                # OpenAI-compatible and native Ollama: probe the provider's model list.
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        data = r.json()
                        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = []

                # Exact match first
                for mid in model_ids:
                    if mid.lower() == model_name.lower():
                        return build_chat_url(base), mid, headers

                # Partial match
                for mid in model_ids:
                    if model_name.lower() in mid.lower() or mid.lower() in model_name.lower():
                        return build_chat_url(base), mid, headers

        raise ValueError(f"Model '{spec}' not found on any configured endpoint")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

async def do_chat_with_model(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Send a message to a specific model and return its response.

    Content format:
      Line 1: model_name (or model_name@endpoint_name)
      Line 2+: the message to send
    """
    from src.llm_core import llm_call_async

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    message = lines[1].strip() if len(lines) > 1 else ""
    if not message:
        return {"error": "No message provided (line 2+ is the message)"}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [{"role": "user", "content": message}],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        # Truncate very long responses
        if len(response) > 10000:
            response = response[:10000] + "\n... (truncated)"
        return {"model": model, "response": response}
    except Exception as e:
        logger.error(f"chat_with_model failed: {e}")
        return {"error": f"Failed to get response from {model_spec}: {e}"}


_TEACHER_SYSTEM_PROMPT = (
    "You are a senior AI mentor. A less capable model is stuck on a problem and asking for help. "
    "Provide clear, actionable guidance:\n"
    "1. Brief analysis of the problem\n"
    "2. Recommended approach (step by step)\n"
    "3. Key things to watch out for\n\n"
    "Be concise and practical. No preamble."
)


async def do_ask_teacher(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Ask a more capable model for help.

    Content format:
      Line 1: model_name (or 'auto')
      Line 2+: the problem description
    """
    from src.llm_core import llm_call_async
    from src.settings import get_setting

    lines = content.strip().split("\n", 1)
    model_spec = lines[0].strip() if lines else "auto"
    problem = lines[1].strip() if len(lines) > 1 else ""

    if not problem:
        return {"error": "No problem description provided"}

    if model_spec.lower() in ("auto", ""):
        model_spec = get_setting("teacher_model", "")
        if not model_spec:
            return {"error": "No teacher model configured. Specify a model name or set teacher_model in settings."}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    try:
        response = await llm_call_async(
            url, model,
            [
                {"role": "system", "content": _TEACHER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Problem:\n{problem}"},
            ],
            headers=headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        if len(response) > 8000:
            response = response[:8000] + "\n... (truncated)"
        return {"model": model, "response": response, "teacher": True}
    except Exception as e:
        logger.error(f"ask_teacher failed: {e}")
        return {"error": f"Teacher call failed ({model_spec}): {e}"}


async def do_second_opinion(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Get a second opinion from another model, then have the original model
    evaluate the feedback and produce a unified version.

    Content format:
      Line 1: model_name (or model_name@endpoint_name)
      Line 2+ (optional): specific question or focus area

    Flow:
      1. Pull recent conversation context
      2. Send to reviewer model → get honest feedback
      3. Send feedback back to the session's own model → evaluate & unify
      4. Return both the review and the unified response
    """
    from src.llm_core import llm_call_async

    lines = content.strip().split("\n", 1)
    if not lines or not lines[0].strip():
        return {"error": "First line must be the model name"}

    model_spec = lines[0].strip()
    focus = lines[1].strip() if len(lines) > 1 else ""

    try:
        reviewer_url, reviewer_model, reviewer_headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    # Pull recent conversation context from current session
    context_text = ""
    sess = None
    if session_id and _session_manager:
        sess = _session_manager.get_session(session_id)
        if sess:
            messages = sess.get_context_messages()
            recent = messages[-15:] if len(messages) > 15 else messages
            parts = []
            for m in recent:
                role = m.get("role", "unknown").upper()
                text = m.get("content", "")
                if isinstance(text, list):
                    text = " ".join(
                        p.get("text", "") for p in text if isinstance(p, dict)
                    )
                if text:
                    parts.append(f"[{role}]: {text[:2000]}")
            context_text = "\n\n".join(parts)

    if not context_text:
        return {"error": "No conversation context found to review"}

    # ── Step 1: Get the reviewer's feedback ──
    reviewer_system = (
        "You are giving a second opinion on a conversation between a user and an AI assistant. "
        "Your job is to be genuinely helpful and honest — not a yes-man, but not a contrarian either.\n\n"
        "Guidelines:\n"
        "- If the plan/idea is solid, say so clearly. Don't manufacture problems that aren't there.\n"
        "- If you spot a real flaw, blind spot, or simpler approach — call it out directly.\n"
        "- Be practical. Don't over-engineer or over-analyze. Real-world tradeoffs matter.\n"
        "- If there's a meaningfully better way to do something, suggest it concretely.\n"
        "- Give credit where it's due — highlight what's working well.\n"
        "- Keep it concise and actionable. No fluff.\n"
        "- You're a second pair of eyes, not a professor grading a paper."
    )

    reviewer_message = f"Here's the conversation so far:\n\n{context_text}"
    if focus:
        reviewer_message += f"\n\n---\nSpecifically, I want your take on: {focus}"
    else:
        reviewer_message += "\n\n---\nGive me your honest second opinion on what's being discussed."

    try:
        review = await llm_call_async(
            reviewer_url, reviewer_model,
            [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": reviewer_message},
            ],
            headers=reviewer_headers,
            timeout=AI_CHAT_TIMEOUT,
        )
        if len(review) > 8000:
            review = review[:8000] + "\n... (truncated)"
    except Exception as e:
        logger.error(f"second_opinion reviewer call failed: {e}")
        return {"error": f"Failed to get second opinion from {model_spec}: {e}"}

    # ── Step 2: Send review back to session's own model for evaluation ──
    unified = ""
    original_model = "unknown"
    if sess:
        original_url = sess.endpoint_url
        original_model = sess.model
        original_headers = getattr(sess, "headers", None) or {}

        unify_system = (
            "Another AI model just reviewed the conversation you've been having with the user. "
            "Read their feedback carefully, then respond with:\n\n"
            "1. **What you agree with** — acknowledge valid points honestly.\n"
            "2. **What you disagree with** — explain why, briefly.\n"
            "3. **Unified version** — produce an updated/refined version of whatever was being discussed, "
            "incorporating the feedback you found valid. Don't accept every note blindly — "
            "use your judgment on what actually improves things vs what's unnecessary.\n\n"
            "Be concise and practical. The user wants a better result, not a meta-discussion."
        )

        unify_message = (
            f"Here's the conversation context:\n\n{context_text}\n\n"
            f"---\n\n"
            f"**Review from {reviewer_model}:**\n\n{review}\n\n"
            f"---\n\n"
            f"Evaluate this feedback and produce a unified improved version."
        )

        try:
            unified = await llm_call_async(
                original_url, original_model,
                [
                    {"role": "system", "content": unify_system},
                    {"role": "user", "content": unify_message},
                ],
                headers=original_headers,
                timeout=AI_CHAT_TIMEOUT,
            )
            if len(unified) > 10000:
                unified = unified[:10000] + "\n... (truncated)"
        except Exception as e:
            logger.error(f"second_opinion unify call failed: {e}")
            unified = f"(Failed to get unified response: {e})"

    # Build combined result
    combined = (
        f"## Second Opinion from {reviewer_model}\n\n{review}"
        f"\n\n---\n\n"
        f"## {original_model}'s Response\n\n{unified}"
    )

    return {
        "model": reviewer_model,
        "response": combined,
        "instruction": "Present these results to the user exactly as they are. Do NOT call second_opinion again. The user can continue the conversation from here.",
    }


async def do_create_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Create a new chat session.

    Content format:
      Line 1: session name
      Line 2: model_name (or model_name@endpoint_name)
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    lines = content.strip().split("\n")
    if len(lines) < 2:
        return {"error": "Need 2 lines: session name, then model spec"}

    name = lines[0].strip()
    model_spec = lines[1].strip()

    if not name:
        return {"error": "Session name cannot be empty"}

    try:
        url, model, headers = _resolve_model(model_spec, owner=owner)
    except ValueError as e:
        return {"error": str(e)}

    sid = str(uuid.uuid4())[:8]
    try:
        _session_manager.create_session(
            session_id=sid,
            name=name,
            endpoint_url=url,
            model=model,
            rag=False,
            owner=owner,
        )
        # Store headers on session for future calls
        sess = _session_manager.get_session(sid)
        if sess and headers:
            sess.headers = headers
        try:
            from src.event_bus import fire_event
            fire_event("session_created", owner)
        except Exception:
            logger.debug("session_created event dispatch failed", exc_info=True)

        return {"session_id": sid, "name": name, "model": model, "endpoint_url": url}
    except Exception as e:
        logger.error(f"create_session failed: {e}")
        return {"error": f"Failed to create session: {e}"}


async def do_list_sessions(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List sessions sorted by most-recently-active first.

    Output includes a relative "last active" timestamp per row so the
    agent can answer "open my last chat" without guessing from titles.
    The most-recent session is always first in the list.

    Content = optional filter keyword (matches session name).
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    keyword = content.strip().lower() if content.strip() else None

    try:
        from core.database import SessionLocal, Session as DbSession
        from datetime import datetime, timezone

        # Pull every session's last_accessed from the DB so we can sort
        # by recency. In-memory sessions hold name + model + msg_count;
        # the DB row holds the timestamps.
        db = SessionLocal()
        try:
            db_rows = {r.id: r for r in db.query(DbSession).all()}
        finally:
            db.close()

        # SECURITY: scope to the caller's sessions. Passing None returned
        # every user's sessions, which the agent tool then exposed via the
        # "list my chats" reply.
        sessions = _session_manager.get_sessions_for_user(owner)
        rows = []
        for sid, sess in sessions.items():
            if keyword and keyword not in (sess.name or "").lower():
                continue
            db_row = db_rows.get(sid)
            # Prefer last_accessed; fall back to updated_at, then created_at.
            ts = None
            if db_row:
                ts = getattr(db_row, 'last_accessed', None) or getattr(db_row, 'updated_at', None) or getattr(db_row, 'created_at', None)
            rows.append((ts, sid, sess))

        # Sort by timestamp DESC; rows without a timestamp sink to the bottom.
        rows.sort(key=lambda r: r[0] or datetime.min, reverse=True)

        def _rel(ts):
            if not ts:
                return 'never'
            now = datetime.utcnow()
            try:
                if ts.tzinfo is not None:
                    now = datetime.now(timezone.utc)
                diff = (now - ts).total_seconds()
            except Exception:
                return 'unknown'
            if diff < 60: return 'just now'
            if diff < 3600: return f'{int(diff / 60)}m ago'
            if diff < 86400: return f'{int(diff / 3600)}h ago'
            if diff < 86400 * 7: return f'{int(diff / 86400)}d ago'
            return ts.strftime('%Y-%m-%d')

        lines = []
        for i, (ts, sid, sess) in enumerate(rows):
            if i >= 50:
                lines.append(f"... and {len(rows) - 50} more (showing first 50)")
                break
            safe_name = (sess.name or "Untitled").replace("[", "\\[").replace("]", "\\]")
            msg_count = getattr(sess, "message_count", 0) or 0
            model = getattr(sess, "model", "unknown")
            marker = " ← most recent" if i == 0 else ""
            lines.append(f"- **[{safe_name}](#session-{sid})** (id: `{sid}`, model: {model}, {msg_count} msgs, last active {_rel(ts)}){marker}")

        if not lines:
            return {"results": "No sessions found" + (f" matching '{keyword}'" if keyword else "") + "."}

        return {
            "results": (
                f"Found {len(rows)} session(s), sorted most-recent first:\n"
                + "\n".join(lines)
                + "\n\nAssistant: when replying to the user, preserve the chat-title markdown links exactly as shown, e.g. `[Chat](#session-id)`. Do not rewrite this as a plain, non-clickable table."
            )
        }
    except Exception as e:
        logger.error(f"list_sessions failed: {e}")
        return {"error": str(e)}


async def do_send_to_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Send a message to an existing session and get a response.

    Content format:
      Line 1: session_id
      Line 2+: message
    """
    from src.llm_core import llm_call_async
    from core.models import ChatMessage

    if not _session_manager:
        return {"error": "Session manager not available"}

    lines = content.strip().split("\n", 1)
    if len(lines) < 2:
        return {"error": "Need 2 lines: session_id, then message"}

    target_sid = lines[0].strip()
    message = lines[1].strip()

    sess = _session_manager.get_session(target_sid)
    if not sess:
        return {"error": f"Session '{target_sid}' not found"}

    # Owner-scope: reject access to another user's session
    if owner and getattr(sess, "owner", None) and sess.owner != owner:
        return {"error": f"Session '{target_sid}' not found"}

    if not message:
        return {"error": "No message provided"}

    try:
        # Build context from session history
        context = sess.get_context_messages()
        context.append({"role": "user", "content": message})

        response = await llm_call_async(
            sess.endpoint_url, sess.model, context,
            headers=sess.headers,
            timeout=AI_CHAT_TIMEOUT,
        )

        # Save both messages to session
        sess.add_message(ChatMessage("user", message))
        sess.add_message(ChatMessage("assistant", response))

        # Truncate for tool output
        if len(response) > 10000:
            response = response[:10000] + "\n... (truncated)"

        return {
            "session_id": target_sid,
            "session_name": sess.name,
            "response": response,
        }
    except Exception as e:
        logger.error(f"send_to_session failed: {e}")
        return {"error": f"Failed to send to session: {e}"}


async def stream_ai_tool(tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None):
    """Dispatcher for streaming AI tools. Yields events as async generator."""
    # Fallback: run non-streaming and yield final result
    desc, result = await dispatch_ai_tool(tool, content, session_id, owner=owner)
    yield {"_final": True, "desc": desc, "result": result}



# ---------------------------------------------------------------------------
# Session management tool
# ---------------------------------------------------------------------------

async def do_manage_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage sessions: rename, archive, delete, important, truncate, fork.

    Content format:
      Line 1: action (rename|archive|unarchive|delete|important|unimportant|truncate|fork)
      Line 2: target session_id (or "current" to use the active session)
      Line 3+: action-specific params (e.g. new name for rename, keep_count for truncate)
    """
    if not _session_manager:
        return {"error": "Session manager not available"}

    from src.database import SessionLocal, Session as DbSession

    # Accept BOTH the structured JSON args the tool schema advertises
    # ({action, session_id, value}) AND the legacy line-based format
    # (line1=action, line2=session_id, line3=value). Native function-calling
    # models send JSON; fenced-block callers send lines. Previously only the
    # line format was parsed, so a model that followed the schema (JSON) got
    # "Need at least 2 lines" / "Rename needs line 3" and couldn't drive it.
    _raw = (content or "").strip()
    action = ""
    target_sid = ""
    value = None      # the action param: new name (rename) / keep_count (truncate, fork)
    _list_filter = ""
    _parsed = None
    if _raw.startswith("{"):
        try:
            _parsed = json.loads(_raw)
        except Exception:
            _parsed = None
    if isinstance(_parsed, dict):
        action = str(_parsed.get("action") or "").strip().lower()
        target_sid = str(_parsed.get("session_id") or _parsed.get("session") or _parsed.get("id") or "").strip()
        _v = _parsed.get("value")
        if _v is None:
            _v = (_parsed.get("name") or _parsed.get("new_name")
                  or _parsed.get("title") or _parsed.get("keep_count"))
        value = None if _v is None else str(_v).strip()
        _list_filter = str(_parsed.get("filter") or "").strip()
    else:
        lines = _raw.split("\n")
        if not lines or not lines[0].strip():
            return {"error": "Missing action (rename|archive|delete|important|truncate|fork|list|switch)"}
        action = lines[0].strip().lower()
        target_sid = lines[1].strip() if len(lines) >= 2 else ""
        value = lines[2].strip() if len(lines) >= 3 else None
        _list_filter = "\n".join(lines[1:]).strip()

    if not action:
        return {"error": "Missing action (rename|archive|delete|important|truncate|fork|list|switch)"}

    # `list` alias — dispatch to do_list_sessions so the agent's natural
    # first guess (every other manage_* tool has a `list` action) works.
    if action == "list":
        return await do_list_sessions(_list_filter, session_id, owner=owner)

    if not target_sid:
        return {"error": "Need a session_id (or 'current' for the active chat)"}

    # Allow "current" to refer to the active session
    if target_sid.lower() == "current" and session_id:
        target_sid = session_id

    # `switch` / `open` / `select` / `view` — the agent reaches for
    # these when the user asks to "open" or "switch to" a session.
    # There's no server-side way to make the browser navigate, so we
    # just return a clickable anchor link the user can click. The
    # frontend's chat-history click delegate routes `#session-<id>`
    # to selectSession(). The agent's reply naturally embeds this
    # result so the user sees a single clickable line.
    def _session_query(db):
        query = db.query(DbSession).filter(DbSession.id == target_sid)
        if owner is not None:
            query = query.filter(DbSession.owner == owner)
        return query

    if action in ("switch", "open", "select", "view"):
        db = SessionLocal()
        try:
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            name = db_sess.name or target_sid
        finally:
            db.close()
        return {
            "action": action,
            "session_id": target_sid,
            "name": name,
            "results": f"[{name}](#session-{target_sid}) — click to open.",
        }

    db = SessionLocal()
    try:
        if action == "rename":
            if not value:
                return {"error": "rename needs a new name (the `value` arg, or line 3 in the legacy format)"}
            new_name = value
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.name = new_name
            db.commit()
            _session_manager.update_session_name(target_sid, new_name)
            return {"action": "rename", "session_id": target_sid, "name": new_name,
                    "results": f"Session renamed to '{new_name}'"}

        elif action == "archive":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.archived = True
            db.commit()
            return {"action": "archive", "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' archived"}

        elif action == "unarchive":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            db_sess.archived = False
            db.commit()
            return {"action": "unarchive", "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' unarchived"}

        elif action == "delete":
            if target_sid == session_id:
                return {"error": "Cannot delete the current session while chatting in it. Delete other sessions first."}
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Refusing to delete an unknown chat id; use the exact id from list_sessions."}
            if db_sess and db_sess.is_important:
                return {"error": f"Session '{db_sess.name}' is starred/favorited. Unstar it first before deleting."}
            try:
                ok = _session_manager.delete_session(target_sid)
                if not ok:
                    return {"error": f"Session '{target_sid}' was not deleted because it no longer exists."}
                return {"action": "delete", "session_id": target_sid,
                        "results": f"Session '{db_sess.name or target_sid}' deleted"}
            except Exception as e:
                return {"error": f"Failed to delete session: {e}"}

        elif action in ("important", "unimportant"):
            is_important = action == "important"
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            # Prevent AI from unstarring sessions — only the user can do that manually
            if not is_important and db_sess.is_important:
                return {"error": f"Session '{db_sess.name}' is starred by the user. Only the user can unstar sessions manually."}
            db_sess.is_important = is_important
            db.commit()
            status = "marked as important" if is_important else "unmarked as important"
            return {"action": action, "session_id": target_sid,
                    "results": f"Session '{db_sess.name}' {status}"}

        elif action == "truncate":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            keep_count = 10
            if value:
                try:
                    keep_count = int(value)
                except ValueError:
                    pass
            success = _session_manager.truncate_messages(target_sid, keep_count)
            if success:
                return {"action": "truncate", "session_id": target_sid,
                        "results": f"Session truncated to last {keep_count} messages"}
            return {"error": f"Failed to truncate session '{target_sid}'"}

        elif action == "fork":
            db_sess = _session_query(db).first()
            if not db_sess:
                return {"error": f"Session '{target_sid}' not found. Use list_sessions and pass the exact id it returned."}
            keep_count = 0  # 0 = all messages
            if value:
                try:
                    keep_count = int(value)
                except ValueError:
                    pass

            source = _session_manager.get_session(target_sid)
            if not source:
                return {"error": f"Session '{target_sid}' not found"}

            new_sid = str(uuid.uuid4())[:8]
            _session_manager.create_session(
                session_id=new_sid,
                name=f"Fork: {source.name}",
                endpoint_url=source.endpoint_url,
                model=source.model,
                rag=False,
                owner=owner,
            )
            # Copy messages
            history = source.get_context_messages()
            if keep_count > 0:
                history = history[:keep_count]
            from core.models import ChatMessage as InMemoryMsg
            new_sess = _session_manager.get_session(new_sid)
            for msg in history:
                new_sess.add_message(InMemoryMsg(msg["role"], msg["content"]))
            try:
                from src.event_bus import fire_event
                fire_event("session_created", owner)
            except Exception:
                logger.debug("session_created event dispatch failed", exc_info=True)

            return {"action": "fork", "session_id": new_sid,
                    "source_session": target_sid, "messages_copied": len(history),
                    "results": f"Forked session '{source.name}' -> new session {new_sid} ({len(history)} messages)"}

        else:
            return {"error": f"Unknown action '{action}'. Use: list, switch, rename, archive, unarchive, delete, important, unimportant, truncate, fork"}
    except Exception as e:
        logger.error(f"manage_session failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# List models tool
# ---------------------------------------------------------------------------

async def do_list_models(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List all available models across configured endpoints.

    Content = optional filter keyword.
    """
    import httpx
    from src.database import SessionLocal, ModelEndpoint
    from src.llm_core import _detect_provider, ANTHROPIC_MODELS
    from src.auth_helpers import owner_filter

    keyword = content.strip().lower() if content.strip() else None

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)
        if owner:
            query = owner_filter(query, ModelEndpoint, owner)
        endpoints = query.all()
        if not endpoints:
            return {"results": "No enabled model endpoints configured."}

        result_lines = []
        total_models = 0

        for ep in endpoints:
            try:
                base, api_key = resolve_endpoint_runtime(ep, owner=owner)
            except Exception:
                continue
            provider = _detect_provider(base)
            headers = build_headers(api_key, base)

            model_ids = []
            if provider == "anthropic":
                model_ids = list(ANTHROPIC_MODELS)
            else:
                try:
                    models_url = build_models_url(base)
                    if models_url:
                        r = httpx.get(models_url, headers=headers, timeout=5)
                        r.raise_for_status()
                        data = r.json()
                        model_ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
                        if not model_ids:
                            model_ids = [
                                m.get("name") or m.get("model")
                                for m in (data.get("models") or [])
                                if m.get("name") or m.get("model")
                            ]
                    else:
                        model_ids = json.loads(ep.cached_models or "[]")
                except Exception:
                    model_ids = ["(endpoint offline)"]

            if keyword:
                model_ids = [m for m in model_ids if keyword in m.lower() or keyword in (ep.name or "").lower()]

            if model_ids:
                result_lines.append(f"\n**{ep.name or base}** ({provider}):")
                for mid in model_ids:
                    result_lines.append(f"  - `{mid}`")
                    total_models += 1

        if not result_lines:
            return {"results": "No models found" + (f" matching '{keyword}'" if keyword else "") + "."}

        header = f"Available models ({total_models} total):"
        return {"results": header + "\n".join(result_lines)}
    except Exception as e:
        logger.error(f"list_models failed: {e}")
        return {"error": str(e)}
    finally:
        db.close()




# ---------------------------------------------------------------------------
# Dispatcher (called from agent_tools.execute_tool_block)
# ---------------------------------------------------------------------------

async def dispatch_ai_tool(
    tool: str, content: str, session_id: Optional[str] = None, owner: Optional[str] = None
) -> Tuple[str, Dict]:
    """Dispatch an AI interaction tool. Returns (description, result_dict)."""

    if tool == "chat_with_model":
        model_spec = content.split("\n")[0].strip()[:60]
        desc = f"chat_with_model: {model_spec}"
        result = await do_chat_with_model(content, session_id, owner=owner)

    elif tool == "create_session":
        name = content.split("\n")[0].strip()[:60]
        desc = f"create_session: {name}"
        result = await do_create_session(content, session_id, owner=owner)

    elif tool == "list_sessions":
        keyword = content.strip()[:40]
        desc = f"list_sessions{': ' + keyword if keyword else ''}"
        result = await do_list_sessions(content, session_id, owner=owner)

    elif tool == "send_to_session":
        sid = content.split("\n")[0].strip()[:20]
        desc = f"send_to_session: {sid}"
        result = await do_send_to_session(content, session_id, owner=owner)

    elif tool == "manage_session":
        action = content.split("\n")[0].strip()[:40]
        desc = f"manage_session: {action}"
        result = await do_manage_session(content, session_id, owner=owner)

    elif tool == "list_models":
        keyword = content.strip()[:40]
        desc = f"list_models{': ' + keyword if keyword else ''}"
        result = await do_list_models(content, session_id, owner=owner)

    elif tool == "ask_teacher":
        problem = content.split("\n", 1)[-1].strip()[:60]
        desc = f"ask_teacher: {problem}"
        result = await do_ask_teacher(content, session_id, owner=owner)

    else:
        desc = f"unknown ai tool: {tool}"
        result = {"error": f"Unknown AI interaction tool: {tool}"}

    return desc, result
