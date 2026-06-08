"""History routes — session history, truncation, fork, conversation topics."""

import json
import uuid
import logging
from typing import Dict, Any

from fastapi import APIRouter, Request, HTTPException

from core.models import ChatMessage
from core.database import SessionLocal, ChatMessage as DbChatMessage, Session as DbSession
from src.topic_analyzer import analyze_topics
from routes.session_routes import (
    _message_role,
    _message_text,
    _reject_compact_during_active_run,
    _verify_session_owner,
)

logger = logging.getLogger(__name__)


def _merge_continue_rows_to_delete(db_messages, db1, db2):
    """DB rows to delete when merging the last two assistant messages.

    Always the second assistant message (db2), plus ONLY the single
    intervening "continue" user message (the one carrying "previous response
    was interrupted") — matching the in-memory merge. The previous code
    deleted the whole index range between the two assistant rows, destroying
    any tool/system/user messages in between and desyncing the DB from the
    in-memory history.
    """
    to_delete = [db2]
    i1 = next((i for i, m in enumerate(db_messages) if m is db1), None)
    i2 = next((i for i, m in enumerate(db_messages) if m is db2), None)
    if i1 is not None and i2 is not None and i2 - 1 > i1:
        between = db_messages[i2 - 1]
        if getattr(between, "role", "") == "user" and            "previous response was interrupted" in (getattr(between, "content", "") or ""):
            to_delete.append(between)
    return to_delete


def setup_history_routes(session_manager) -> APIRouter:
    router = APIRouter(tags=["history"])

    @router.get("/api/history/{session_id}")
    async def get_session_history(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, f"Session '{session_id}' not found")

        history_dict = []
        for msg in session.history:
            if isinstance(msg, ChatMessage):
                # Skip hidden messages (e.g. compaction summaries for AI context)
                if msg.metadata and msg.metadata.get("hidden"):
                    continue
                entry = {"role": msg.role, "content": msg.content}
                if msg.metadata:
                    entry["metadata"] = msg.metadata
                history_dict.append(entry)
            elif isinstance(msg, dict):
                if msg.get("metadata", {}).get("hidden"):
                    continue
                entry = {
                    "role": msg.get("role", ""),
                    "content": msg.get("content", ""),
                }
                if msg.get("metadata"):
                    entry["metadata"] = msg["metadata"]
                history_dict.append(entry)

        # Fallback: load from DB if in-memory is empty
        if not history_dict:
            db = SessionLocal()
            try:
                db_messages = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id)
                    .order_by(DbChatMessage.timestamp)
                    .all()
                )
                import json as _json
                db_history = []
                for m in db_messages:
                    entry = {"role": m.role, "content": m.content}
                    meta = {}
                    if m.meta_data:
                        try:
                            meta = _json.loads(m.meta_data) or {}
                        except (json.JSONDecodeError, ValueError):
                            meta = {}
                    if m.timestamp and "timestamp" not in meta:
                        meta["timestamp"] = m.timestamp.isoformat() + "Z"
                    if meta:
                        entry["metadata"] = meta
                    db_history.append(entry)
                if db_history:
                    # Rebuild in-memory history from the full set so hidden
                    # messages (e.g. compaction summaries) are kept for AI context.
                    session.history = [
                        ChatMessage(role=m["role"], content=m["content"], metadata=m.get("metadata"))
                        for m in db_history
                    ]
                # Response excludes hidden messages, matching the in-memory path.
                history_dict = [
                    m for m in db_history
                    if not (m.get("metadata") or {}).get("hidden")
                ]
            except Exception as e:
                logger.error(f"DB fallback failed for {session_id}: {e}")
            finally:
                db.close()

        return {
            "history": history_dict,
            "model": session.model,
            "endpoint_url": session.endpoint_url,
            "name": session.name,
        }

    @router.post("/api/session/{session_id}/truncate")
    async def truncate_session(request: Request, session_id: str):
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            keep_count = body.get("keep_count", 0)
            result = session_manager.truncate_messages(session_id, keep_count)
            return {"status": "ok", "kept": keep_count, "truncated": result}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except Exception as e:
            logger.error(f"Truncate error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/message")
    async def add_message(request: Request, session_id: str):
        """Add a message to a session (for slash command persistence)."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            role = body.get("role", "assistant")
            content = body.get("content", "")
            if not content:
                raise HTTPException(400, "content is required")
            msg = ChatMessage(role=role, content=content, metadata=body.get("metadata"))
            session_manager.add_message(session_id, msg)
            return {"status": "ok"}
        except KeyError:
            raise HTTPException(404, "Session not found")

    @router.post("/api/session/{session_id}/delete-messages")
    async def delete_messages(request: Request, session_id: str):
        """Delete specific messages by DB ID (or legacy index)."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            msg_ids = body.get("msg_ids", [])
            indices = body.get("indices")  # legacy fallback

            session = session_manager.get_session(session_id)
            db = SessionLocal()
            try:
                if msg_ids:
                    # New ID-based delete
                    deleted = 0
                    for mid in msg_ids:
                        db_msg = db.query(DbChatMessage).filter(
                            DbChatMessage.id == mid,
                            DbChatMessage.session_id == session_id,
                        ).first()
                        if db_msg:
                            db.delete(db_msg)
                            deleted += 1

                    # Remove from in-memory history by matching _db_id
                    def _get_db_id(m):
                        meta = m.metadata if isinstance(m, ChatMessage) else (m.get('metadata') if isinstance(m, dict) else None)
                        return meta.get('_db_id') if isinstance(meta, dict) else None
                    session.history = [m for m in session.history if _get_db_id(m) not in msg_ids]
                elif indices:
                    # Legacy index-based delete
                    indices = sorted(indices, reverse=True)
                    db_messages = db.query(DbChatMessage).filter(
                        DbChatMessage.session_id == session_id
                    ).order_by(DbChatMessage.timestamp).all()

                    deleted = 0
                    for idx in indices:
                        if 0 <= idx < len(db_messages):
                            db.delete(db_messages[idx])
                            deleted += 1
                        if 0 <= idx < len(session.history):
                            session.history.pop(idx)
                else:
                    return {"status": "ok", "deleted": 0}

                session.message_count = len(session.history)
                db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
                if db_session:
                    db_session.message_count = len(session.history)
                    from datetime import datetime, timezone
                    db_session.updated_at = datetime.now(timezone.utc)

                db.commit()
                return {"status": "ok", "deleted": deleted}
            finally:
                db.close()
        except KeyError:
            raise HTTPException(404, "Session not found")
        except Exception as e:
            logger.error(f"Delete messages error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/edit-message")
    async def edit_message(request: Request, session_id: str):
        """Edit the content of a message by its database ID."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            msg_id = body.get("msg_id")
            content = body.get("content")
            if not msg_id or content is None:
                raise HTTPException(400, "msg_id and content are required")

            session = session_manager.get_session(session_id)
            db = SessionLocal()
            try:
                db_msg = db.query(DbChatMessage).filter(
                    DbChatMessage.id == msg_id,
                    DbChatMessage.session_id == session_id,
                ).first()
                if not db_msg:
                    raise HTTPException(404, "Message not found")

                db_msg.content = content
                meta = {}
                if db_msg.meta_data:
                    try: meta = json.loads(db_msg.meta_data)
                    except (json.JSONDecodeError, ValueError): pass
                meta['edited'] = True
                db_msg.meta_data = json.dumps(meta)

                # Update in-memory history by matching _db_id
                for hmsg in session.history:
                    hmeta = hmsg.metadata if isinstance(hmsg, ChatMessage) else hmsg.get('metadata')
                    if isinstance(hmeta, dict) and hmeta.get('_db_id') == msg_id:
                        if isinstance(hmsg, ChatMessage):
                            hmsg.content = content
                            hmsg.metadata['edited'] = True
                        elif isinstance(hmsg, dict):
                            hmsg['content'] = content
                            hmsg['metadata']['edited'] = True
                        break

                db.commit()
                return {"status": "ok"}
            finally:
                db.close()
        except KeyError:
            raise HTTPException(404, "Session not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Edit message error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/mark-stopped")
    async def mark_stopped(request: Request, session_id: str):
        """Mark the last assistant message as stopped by user."""
        _verify_session_owner(request, session_id)
        try:
            session = session_manager.get_session(session_id)
            # Find last assistant message and add stopped metadata
            for msg in reversed(session.history):
                if (isinstance(msg, ChatMessage) and msg.role == 'assistant') or \
                   (isinstance(msg, dict) and msg.get('role') == 'assistant'):
                    if isinstance(msg, ChatMessage):
                        if not msg.metadata:
                            msg.metadata = {}
                        msg.metadata['stopped'] = True
                        if not msg.metadata.get('model'):
                            msg.metadata['model'] = session.model
                    else:
                        if 'metadata' not in msg:
                            msg['metadata'] = {}
                        msg['metadata']['stopped'] = True
                        if not msg['metadata'].get('model'):
                            msg['metadata']['model'] = session.model
                    break
            # Also update in DB
            db = SessionLocal()
            try:
                import json as _json
                db_messages = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id, DbChatMessage.role == 'assistant')
                    .order_by(DbChatMessage.timestamp.desc())
                    .first()
                )
                if db_messages:
                    meta = {}
                    if db_messages.meta_data:
                        try:
                            meta = _json.loads(db_messages.meta_data)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    meta['stopped'] = True
                    if not meta.get('model'):
                        meta['model'] = session.model
                    db_messages.meta_data = _json.dumps(meta)
                    db.commit()
            finally:
                db.close()
            session_manager.save_sessions()
            return {"status": "ok"}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except Exception as e:
            logger.error(f"Mark stopped error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/update-last-meta")
    async def update_last_meta(request: Request, session_id: str):
        """Merge metadata into the last assistant message (e.g. save variants)."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            meta_update = body.get("metadata", {})
            session = session_manager.get_session(session_id)

            # Update in-memory
            for msg in reversed(session.history):
                if (isinstance(msg, ChatMessage) and msg.role == 'assistant') or \
                   (isinstance(msg, dict) and msg.get('role') == 'assistant'):
                    if isinstance(msg, ChatMessage):
                        if not msg.metadata:
                            msg.metadata = {}
                        msg.metadata.update(meta_update)
                    else:
                        if 'metadata' not in msg:
                            msg['metadata'] = {}
                        msg['metadata'].update(meta_update)
                    break

            # Update in DB
            db = SessionLocal()
            try:
                import json as _json
                db_msg = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id, DbChatMessage.role == 'assistant')
                    .order_by(DbChatMessage.timestamp.desc())
                    .first()
                )
                if db_msg:
                    meta = {}
                    if db_msg.meta_data:
                        try: meta = _json.loads(db_msg.meta_data)
                        except (json.JSONDecodeError, ValueError): pass
                    meta.update(meta_update)
                    db_msg.meta_data = _json.dumps(meta)
                    db.commit()
            finally:
                db.close()
            session_manager.save_sessions()
            return {"status": "ok"}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except Exception as e:
            logger.error(f"Update last meta error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/merge-last-assistant")
    async def merge_last_assistant(request: Request, session_id: str):
        """Merge the last two assistant messages into one (for continue)."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            separator = body.get("separator", "\n\n")
            session = session_manager.get_session(session_id)

            # Find last two assistant messages in-memory
            ai_indices = []
            for i, msg in enumerate(session.history):
                role = msg.role if isinstance(msg, ChatMessage) else msg.get('role', '')
                if role == 'assistant':
                    ai_indices.append(i)

            if len(ai_indices) < 2:
                return {"status": "ok", "merged": False}

            idx1, idx2 = ai_indices[-2], ai_indices[-1]
            msg1, msg2 = session.history[idx1], session.history[idx2]

            content1 = msg1.content if isinstance(msg1, ChatMessage) else msg1.get('content', '')
            content2 = msg2.content if isinstance(msg2, ChatMessage) else msg2.get('content', '')
            merged_content = content1 + separator + content2

            # Merge metadata
            meta1 = (msg1.metadata if isinstance(msg1, ChatMessage) else msg1.get('metadata')) or {}
            meta2 = (msg2.metadata if isinstance(msg2, ChatMessage) else msg2.get('metadata')) or {}
            merged_meta = {**meta1, **meta2}
            merged_meta.pop('stopped', None)  # no longer stopped after continue

            # Update first message, remove second
            if isinstance(msg1, ChatMessage):
                msg1.content = merged_content
                msg1.metadata = merged_meta
            else:
                msg1['content'] = merged_content
                msg1['metadata'] = merged_meta

            # Also remove the hidden "continue" user message between them if present
            # It's the message at idx2-1 if it's a user message with continue text
            remove_indices = [idx2]
            if idx2 - 1 > idx1:
                between = session.history[idx2 - 1]
                between_role = between.role if isinstance(between, ChatMessage) else between.get('role', '')
                between_content = between.content if isinstance(between, ChatMessage) else between.get('content', '')
                if between_role == 'user' and 'previous response was interrupted' in between_content:
                    remove_indices.insert(0, idx2 - 1)

            for ri in sorted(remove_indices, reverse=True):
                session.history.pop(ri)

            # Update DB
            db = SessionLocal()
            try:
                import json as _json
                db_messages = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id)
                    .order_by(DbChatMessage.timestamp)
                    .all()
                )
                # Find last two assistant messages in DB
                ai_db = [(i, m) for i, m in enumerate(db_messages) if m.role == 'assistant']
                if len(ai_db) >= 2:
                    (_, db1), (_, db2) = ai_db[-2], ai_db[-1]
                    db1.content = merged_content
                    db1.meta_data = _json.dumps(merged_meta)

                    # Mirror the in-memory deletion: remove the second assistant
                    # message and ONLY the "continue" user message between them
                    # (not arbitrary tool/system/user rows). The old
                    # range-delete destroyed every row between the two assistant
                    # messages, desyncing the DB from the in-memory history.
                    for _row in _merge_continue_rows_to_delete(db_messages, db1, db2):
                        db.delete(_row)

                    db.commit()
            finally:
                db.close()
            session_manager.save_sessions()
            return {"status": "ok", "merged": True}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except Exception as e:
            logger.error(f"Merge assistant error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/fork")
    async def fork_session(request: Request, session_id: str):
        """Create a new session with messages copied up to keep_count."""
        _verify_session_owner(request, session_id)
        try:
            body = await request.json()
            keep_count = body.get("keep_count", 0)

            # Get the source session
            source = session_manager.sessions.get(session_id)
            if not source:
                raise HTTPException(404, "Session not found")

            # Create new session
            new_id = str(uuid.uuid4())
            fork_name = f"\u2ADD {source.name}"
            new_session = session_manager.create_session(
                session_id=new_id,
                name=fork_name,
                endpoint_url=source.endpoint_url,
                model=source.model,
                rag=False,
                owner=getattr(source, 'owner', None),
            )

            # Copy messages up to keep_count
            msgs_to_copy = source.history[:keep_count]
            for msg in msgs_to_copy:
                new_session.add_message(ChatMessage(msg.role, msg.content, msg.metadata))
            try:
                from src.event_bus import fire_event
                fire_event("session_created", getattr(source, 'owner', None))
            except Exception:
                logger.debug("session_created event dispatch failed", exc_info=True)

            return {
                "status": "ok",
                "id": new_id,
                "name": fork_name,
                "kept": len(msgs_to_copy),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Fork error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.get("/api/conversations/topics")
    async def get_conversation_topics(request: Request) -> Dict[str, Any]:
        from src.auth_helpers import require_user
        user = require_user(request)
        try:
            return analyze_topics(session_manager, owner=user or None)
        except Exception as e:
            raise HTTPException(500, f"Topic analysis failed: {e}")

    @router.post("/api/session/{session_id}/compact")
    async def compact_session(request: Request, session_id: str):
        """Manually trigger context compaction for a session."""
        _verify_session_owner(request, session_id)
        from src.auth_helpers import effective_user
        owner = effective_user(request)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")
        _reject_compact_during_active_run(session_id)

        try:
            from src.model_context import estimate_tokens, get_context_length
            from src.llm_core import llm_call_async
            from src.endpoint_resolver import resolve_endpoint

            if len(session.history) < 6:
                return {"status": "ok", "message": "Not enough messages to compact"}

            ctx_len = get_context_length(session.endpoint_url, session.model)
            messages_before = session.get_context_messages()
            used_before = estimate_tokens(messages_before)
            pct_before = round((used_before / ctx_len) * 100, 1) if ctx_len else 0
            msg_count_before = len(session.history)

            # Keep only last 4 messages, summarize the rest
            keep_count = 4
            older = session.history[:-keep_count]
            recent = session.history[-keep_count:]

            # Build text to summarize
            convo_text = "\n".join(
                f"{_message_role(m).upper()}: "
                f"{_message_text(m)[:2000]}"
                for m in older
            )

            # Use utility model if available
            util_url, util_model, util_headers = resolve_endpoint("utility", owner=owner or None)
            compact_url = util_url or session.endpoint_url
            compact_model = util_model or session.model
            compact_headers = util_headers if util_url else session.headers

            from src.context_compactor import SELF_SUMMARY_SYSTEM_PROMPT
            compaction_count = sum(1 for m in session.history if isinstance(m, ChatMessage) and "[Conversation summary" in (m.content or ""))
            sys_prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace("{count}", str(len(older))).replace("{n}", str(compaction_count + 1))
            summary = await llm_call_async(
                compact_url, compact_model,
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": convo_text},
                ],
                temperature=0.2, max_tokens=1024,
                headers=compact_headers, timeout=30,
            )

            # Replace session history: summary as system message + recent messages
            # System message holds the full summary for AI context
            system_summary = ChatMessage(
                role="system",
                content=f"[Conversation summary — {len(older)} earlier messages were compacted]\n\n{summary}",
                metadata={"compacted": True, "hidden": True},
            )
            # Visible assistant message just shows stats
            summary_msg = ChatMessage(
                role="assistant",
                content=f"**Conversation compacted** — {len(older)} messages summarized, {len(recent)} kept.",
                metadata={"compacted": True, "messages_removed": len(older)},
            )
            new_history = [system_summary, summary_msg] + list(recent)
            session.history = new_history
            session.message_count = len(session.history)
            logger.info(f"Compact: session {session_id} history now has {len(session.history)} messages (was {msg_count_before})")

            # Update DB: delete old messages, insert summary
            db = SessionLocal()
            try:
                db_msgs = db.query(DbChatMessage).filter(
                    DbChatMessage.session_id == session_id
                ).order_by(DbChatMessage.timestamp).all()

                # Delete all but the last keep_count
                for m in db_msgs[:-keep_count]:
                    db.delete(m)

                # Insert system summary (hidden, for AI context) and visible summary
                import json as _json
                import uuid
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                db_sys_summary = DbChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="system",
                    content=system_summary.content,
                    meta_data=_json.dumps(system_summary.metadata),
                    timestamp=now,
                )
                db.add(db_sys_summary)
                db_summary = DbChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=summary_msg.content,
                    meta_data=_json.dumps(summary_msg.metadata),
                    timestamp=now,
                )
                db.add(db_summary)

                # Update session record
                db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
                if db_session:
                    db_session.message_count = len(session.history)
                    db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
            finally:
                db.close()

            session_manager.save_sessions()

            used_after = estimate_tokens(session.get_context_messages())
            pct_after = round((used_after / ctx_len) * 100, 1) if ctx_len else 0

            return {
                "status": "ok",
                "message": f"Compacted: {msg_count_before} msgs → {len(session.history)} msgs ({pct_before}% → {pct_after}%)",
                "before": pct_before,
                "after": pct_after,
            }

        except Exception as e:
            logger.error(f"Manual compact error {session_id}: {e}")
            raise HTTPException(500, str(e))

    return router
