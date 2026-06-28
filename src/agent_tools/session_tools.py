"""session_tools.py - agent tools for AI-to-AI session management.

Owns create_session, list_sessions, send_to_session and manage_session, moved
out of src.ai_interaction as part of the tool -> registry migration (#3629), and
their handler classes registered in TOOL_HANDLERS.

The session manager is a runtime-set singleton in src.ai_interaction, so each
function fetches it via get_session_manager() (imported here); _resolve_model and
AI_CHAT_TIMEOUT are reused from there too.
"""
import asyncio
import json
import logging
import uuid
from typing import Dict, Optional

from src.ai_interaction import get_session_manager, _resolve_model, AI_CHAT_TIMEOUT

logger = logging.getLogger(__name__)


async def create_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Create a new chat session.

    Content format:
      Line 1: session name
      Line 2: model_name (or model_name@endpoint_name)
    """
    _session_manager = get_session_manager()
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
        url, model, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=owner)
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

async def list_sessions(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List sessions sorted by most-recently-active first.

    Output includes a relative "last active" timestamp per row so the
    agent can answer "open my last chat" without guessing from titles.
    The most-recent session is always first in the list.

    Content = optional filter keyword (matches session name).
    """
    _session_manager = get_session_manager()
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

async def send_to_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Send a message to an existing session and get a response.

    Content format:
      Line 1: session_id
      Line 2+: message
    """
    _session_manager = get_session_manager()
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

async def manage_session(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """Manage sessions: rename, archive, delete, important, truncate, fork.

    Content format:
      Line 1: action (rename|archive|unarchive|delete|important|unimportant|truncate|fork)
      Line 2: target session_id (or "current" to use the active session)
      Line 3+: action-specific params (e.g. new name for rename, keep_count for truncate)
    """
    _session_manager = get_session_manager()
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

    # `list` alias - dispatch to list_sessions so the agent's natural
    # first guess (every other manage_* tool has a `list` action) works.
    if action == "list":
        return await list_sessions(_list_filter, session_id, owner=owner)

    if not target_sid:
        return {"error": "Need a session_id (or 'current' for the active chat)"}

    # Allow "current" to refer to the active session
    if target_sid.lower() == "current" and session_id:
        target_sid = session_id

    # `switch` / `open` / `select` / `view` - the agent reaches for
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
            "results": f"[{name}](#session-{target_sid}) - click to open.",
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
            # Prevent AI from unstarring sessions - only the user can do that manually
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
# Handler classes registered in TOOL_HANDLERS
# ---------------------------------------------------------------------------

class CreateSessionTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await create_session(content, ctx.get("session_id"), owner=ctx.get("owner"))


class ListSessionsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await list_sessions(content, ctx.get("session_id"), owner=ctx.get("owner"))


class SendToSessionTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await send_to_session(content, ctx.get("session_id"), owner=ctx.get("owner"))


class ManageSessionTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await manage_session(content, ctx.get("session_id"), owner=ctx.get("owner"))
