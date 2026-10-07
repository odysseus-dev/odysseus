"""History routes — session history, truncation, fork, conversation topics."""

import json
import os
import uuid
import logging
import re
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException, Depends

from core.models import ChatMessage
from core.database import SessionLocal, ChatMessage as DbChatMessage, Session as DbSession
from src.auth_helpers import effective_user, require_chat_api_token_scope
from src.topic_analyzer import analyze_topics
from src.upload_handler import reserve_message_upload_references
from src.tool_approval_scopes import sanitize_client_message_metadata
from routes.session_routes import (
    _message_role,
    _message_text,
    _reject_compact_during_active_run,
    _verify_session_owner,
)
from routes.chat_helpers import strip_tui_local_context

logger = logging.getLogger(__name__)

_HISTORY_INLINE_MEDIA_THRESHOLD = 200_000
_DATA_IMAGE_RE = re.compile(r"data:image/[^;,\"]+;base64,[A-Za-z0-9+/=\s]+")


def _sft_trace_file_for_owner(owner: str | None) -> str | None:
    if not str(owner or "").startswith("sft_"):
        return None
    flag = os.getenv("ODYSSEUS_SFT_TRACE_CAPTURE", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return None
    try:
        from src.constants import DATA_DIR
        trace_dir = os.getenv("ODYSSEUS_SFT_TRACE_DIR") or os.path.join(DATA_DIR, "sft_traces")
        return os.path.join(trace_dir, f"{owner}.jsonl")
    except Exception:
        return None


def _remove_deleted_sft_trace_rows(
    *,
    owner: str | None,
    session_id: str,
    deleted_pairs: list[dict[str, str]],
) -> None:
    """Keep the training JSONL aligned with user-deleted chat attempts."""
    path = _sft_trace_file_for_owner(owner)
    if not path or not deleted_pairs or not os.path.exists(path):
        return
    try:
        kept: list[str] = []
        removed: list[str] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    kept.append(raw)
                    continue
                if row.get("session_id") != session_id:
                    kept.append(raw)
                    continue
                row_user = str(row.get("user") or "").strip()
                row_assistant = str(row.get("assistant") or "").strip()
                should_remove = any(
                    row_user == pair.get("user", "").strip()
                    and row_assistant == pair.get("assistant", "").strip()
                    for pair in deleted_pairs
                )
                if should_remove:
                    tombstone = dict(row)
                    tombstone["deleted_from_training"] = True
                    removed.append(json.dumps(tombstone, ensure_ascii=False))
                else:
                    kept.append(raw)
        with open(path, "w", encoding="utf-8") as f:
            for raw in kept:
                f.write(raw + "\n")
        if removed:
            trash_path = path + ".trash"
            with open(trash_path, "a", encoding="utf-8") as f:
                for raw in removed:
                    f.write(raw + "\n")
            logger.info(
                "Removed %d SFT trace row(s) for deleted messages in session %s",
                len(removed),
                session_id,
            )
    except Exception as exc:
        logger.warning("Failed to prune SFT trace rows for %s: %s", session_id, exc)


def _deleted_sft_pairs_from_db_rows(rows: list[DbChatMessage]) -> list[dict[str, str]]:
    """Build user/assistant pairs affected by deleted messages.

    The SFT trace row is one assistant turn paired with the nearest preceding
    user turn. If the user deletes either side of a failed attempt before
    retrying, remove that pair from the training JSONL.
    """
    pairs: list[dict[str, str]] = []
    last_user = ""
    pending_deleted_user = ""
    for row in rows:
        role = str(getattr(row, "role", "") or "")
        content = str(getattr(row, "content", "") or "").strip()
        will_delete = bool(getattr(row, "_will_delete_for_sft", False))
        if role == "user":
            last_user = content
            if will_delete:
                pending_deleted_user = content
            continue
        if role != "assistant":
            continue
        if will_delete and last_user:
            pairs.append({"user": last_user, "assistant": content})
        elif pending_deleted_user:
            pairs.append({"user": pending_deleted_user, "assistant": content})
            pending_deleted_user = ""
    return pairs


def _history_display_content(content: Any) -> Any:
    """Return a lightweight browser-display copy of stored message content.

    Older multimodal user messages may be persisted as a JSON *string*
    containing image_url blocks with inline base64 image bytes. Those bytes are
    needed for model calls when the turn is first sent, but they should not be
    sent back through /api/history every time the user opens the chat. The
    attachment metadata already carries file ids/names for the UI cards.
    """
    if isinstance(content, list):
        text_parts = []
        omitted_media = 0
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
            elif block.get("type") in {"image_url", "input_image", "audio", "input_audio"}:
                omitted_media += 1
        text = "\n".join(text_parts).strip()
        if omitted_media and not text:
            return f"[{omitted_media} media attachment{'s' if omitted_media != 1 else ''} omitted from history view]"
        return text

    if not isinstance(content, str):
        return content
    if len(content) < _HISTORY_INLINE_MEDIA_THRESHOLD and "data:image/" not in content:
        return content

    stripped = content.lstrip()
    if stripped.startswith("["):
        try:
            blocks = json.loads(content)
        except (json.JSONDecodeError, TypeError, ValueError):
            blocks = None
        if isinstance(blocks, list):
            text_parts = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        text_parts.append(text)
            if text_parts:
                return "\n".join(text_parts).strip()

    if "data:image/" in content:
        return _DATA_IMAGE_RE.sub("[inline image omitted from history view]", content)
    return content


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


def _is_continue_interruption_message(message: Any) -> bool:
    if isinstance(message, ChatMessage):
        role = message.role
        content = message.content
    elif isinstance(message, dict):
        role = message.get("role", "")
        content = message.get("content", "")
    else:
        role = getattr(message, "role", "")
        content = getattr(message, "content", "")
    normalized = " ".join(str(content or "").strip().lower().split())
    return role == "user" and (
        "previous response was interrupted" in normalized
        or normalized in {
            "continue from where you left off.",
            "continue from where you left off",
        }
    )


def _has_immediate_continue_marker(messages: list[Any], idx1: int, idx2: int) -> bool:
    return idx2 - idx1 == 2 and _is_continue_interruption_message(messages[idx1 + 1])


def _keep_count_before_message(db_messages, before_msg_id: str | None) -> int | None:
    """Return the durable-history keep count before a DB message id."""
    wanted = str(before_msg_id or "").strip()
    if not wanted:
        return None
    for pos, row in enumerate(db_messages):
        if str(getattr(row, "id", "")) == wanted:
            return pos
    return None


def setup_history_routes(session_manager, upload_handler=None) -> APIRouter:
    router = APIRouter(
        tags=["history"],
        dependencies=[Depends(require_chat_api_token_scope)],
    )

    def _reserve_message_uploads(
        request: Request,
        content: Any,
        metadata: Any = None,
    ) -> None:
        try:
            missing_id = reserve_message_upload_references(
                upload_handler,
                effective_user(request),
                content,
                metadata,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "Invalid message attachment metadata") from exc
        if missing_id:
            raise HTTPException(
                409,
                f"Referenced upload is no longer available: {missing_id}",
            )

    def _db_history_entry(m: DbChatMessage) -> Dict[str, Any]:
        entry = {"role": m.role, "content": strip_tui_local_context(_history_display_content(m.content))}
        meta = {}
        if m.meta_data:
            try:
                meta = json.loads(m.meta_data) or {}
            except (json.JSONDecodeError, ValueError):
                meta = {}
        meta["_db_id"] = m.id
        if m.timestamp and "timestamp" not in meta:
            meta["timestamp"] = m.timestamp.isoformat() + "Z"
        if meta:
            entry["metadata"] = meta
        return entry

    @router.get("/api/history/{session_id}")
    async def get_session_history(
        request: Request,
        session_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Dict[str, Any]:
        _verify_session_owner(request, session_id)
        if limit is not None:
            page_limit = max(1, min(int(limit), 100))
            db = SessionLocal()
            try:
                db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
                if db_session is None:
                    raise HTTPException(404, f"Session '{session_id}' not found")

                total = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id)
                    .count()
                )
                page_offset = int(offset) if offset is not None else max(total - page_limit, 0)
                page_offset = max(0, min(page_offset, total))
                # Keep display pagination page-scoped. ``get_session`` is the
                # full model-context hydration seam and must not be entered here.
                rows = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id)
                    .order_by(DbChatMessage.timestamp)
                    .offset(page_offset)
                    .limit(page_limit)
                    .all()
                )
                history_dict = [
                    entry for entry in (_db_history_entry(m) for m in rows)
                    if not (entry.get("metadata") or {}).get("hidden")
                ]
                return {
                    "history": history_dict,
                    "model": db_session.model,
                    "endpoint_url": db_session.endpoint_url,
                    "endpoint_id": getattr(db_session, "endpoint_id", None),
                    "name": db_session.name,
                    "offset": page_offset,
                    "limit": page_limit,
                    "total": total,
                    "has_more_before": page_offset > 0,
                    "has_more_after": page_offset + len(rows) < total,
                }
            finally:
                db.close()

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
                entry = {"role": msg.role, "content": strip_tui_local_context(_history_display_content(msg.content))}
                if msg.metadata:
                    entry["metadata"] = msg.metadata
                history_dict.append(entry)
            elif isinstance(msg, dict):
                if msg.get("metadata", {}).get("hidden"):
                    continue
                entry = {
                    "role": msg.get("role", ""),
                    "content": strip_tui_local_context(_history_display_content(msg.get("content", ""))),
                }
                if msg.get("metadata"):
                    entry["metadata"] = msg["metadata"]
                history_dict.append(entry)

        # Fallback: load from DB if in-memory renders empty. Display only —
        # get_session above is the hydration seam, so nothing here writes back
        # into session.history — rebuilding it from raw rows would overwrite
        # parsed multimodal content and the _db_id edit/delete keys it just set.
        if not history_dict:
            db = SessionLocal()
            try:
                db_messages = (
                    db.query(DbChatMessage)
                    .filter(DbChatMessage.session_id == session_id)
                    .order_by(DbChatMessage.timestamp)
                    .all()
                )
                # Response excludes hidden messages, matching the in-memory path.
                history_dict = [
                    entry for entry in (_db_history_entry(m) for m in db_messages)
                    if not (entry.get("metadata") or {}).get("hidden")
                ]
            except Exception as e:
                logger.error(f"DB fallback failed for {session_id}: {e}")
            finally:
                db.close()

        return {
            "history": history_dict,
            "model": session.model,
            "endpoint_url": session.endpoint_url,
            "endpoint_id": getattr(session, "endpoint_id", None),
            "name": session.name,
        }

    @router.post("/api/session/{session_id}/truncate")
    async def truncate_session(request: Request, session_id: str):
        _verify_session_owner(request, session_id)
        try:
            try:
                body = await request.json()
            except json.JSONDecodeError:
                raise HTTPException(400, "Request body must be valid JSON")
            if not isinstance(body, dict):
                raise HTTPException(400, "Request body must be a JSON object")
            before_msg_id = body.get("before_msg_id") or body.get("message_id") or ""
            if not isinstance(before_msg_id, str):
                raise HTTPException(400, "Message ID must be a string")
            before_msg_id = before_msg_id.strip()
            if "keep_count" not in body and not before_msg_id:
                raise HTTPException(400, "keep_count or before_msg_id required")
            raw_count = body.get("keep_count", 0)
            # Keep integer-string clients working, but do not silently convert
            # booleans or fractional numbers into destructive message counts.
            if type(raw_count) not in (int, str):
                raise HTTPException(400, "keep_count must be a non-negative integer")
            try:
                keep_count = int(raw_count)
            except ValueError:
                raise HTTPException(400, "keep_count must be a non-negative integer")
            if keep_count < 0:
                raise HTTPException(400, "keep_count must be a non-negative integer")
            deleted_sft_pairs: list[dict[str, str]] = []
            if keep_count >= 0:
                db = SessionLocal()
                try:
                    all_db_messages = db.query(DbChatMessage).filter(
                        DbChatMessage.session_id == session_id
                    ).order_by(DbChatMessage.timestamp).all()
                    if before_msg_id:
                        resolved_keep_count = _keep_count_before_message(all_db_messages, before_msg_id)
                        if resolved_keep_count is None:
                            raise HTTPException(404, "Message not found")
                        keep_count = resolved_keep_count
                    for pos, row in enumerate(all_db_messages):
                        row._will_delete_for_sft = pos >= keep_count
                    deleted_sft_pairs = _deleted_sft_pairs_from_db_rows(all_db_messages)
                finally:
                    db.close()
            result = session_manager.truncate_messages(session_id, keep_count)
            _remove_deleted_sft_trace_rows(
                owner=effective_user(request),
                session_id=session_id,
                deleted_pairs=deleted_sft_pairs,
            )
            return {"status": "ok", "kept": keep_count, "truncated": result}
        except KeyError:
            raise HTTPException(404, "Session not found")
        except HTTPException:
            raise
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
            metadata = sanitize_client_message_metadata(body.get("metadata"))
            _reserve_message_uploads(request, content, metadata)
            msg = ChatMessage(role=role, content=content, metadata=metadata)
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
                all_db_messages = db.query(DbChatMessage).filter(
                    DbChatMessage.session_id == session_id
                ).order_by(DbChatMessage.timestamp).all()
                delete_id_set = set(msg_ids or [])
                delete_index_set = set(indices or [])
                for pos, row in enumerate(all_db_messages):
                    row._will_delete_for_sft = (
                        (bool(delete_id_set) and row.id in delete_id_set)
                        or (not delete_id_set and bool(delete_index_set) and pos in delete_index_set)
                    )
                deleted_sft_pairs = _deleted_sft_pairs_from_db_rows(all_db_messages)

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
                _remove_deleted_sft_trace_rows(
                    owner=effective_user(request),
                    session_id=session_id,
                    deleted_pairs=deleted_sft_pairs,
                )
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

            _reserve_message_uploads(request, content)

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
            if not _has_immediate_continue_marker(session.history, idx1, idx2):
                return {"status": "ok", "merged": False, "reason": "no_continue_marker"}

            msg1, msg2 = session.history[idx1], session.history[idx2]

            content1 = msg1.content if isinstance(msg1, ChatMessage) else msg1.get('content', '')
            content2 = msg2.content if isinstance(msg2, ChatMessage) else msg2.get('content', '')
            merged_content = content1 + separator + content2

            # Merge metadata
            meta1 = (msg1.metadata if isinstance(msg1, ChatMessage) else msg1.get('metadata')) or {}
            meta2 = (msg2.metadata if isinstance(msg2, ChatMessage) else msg2.get('metadata')) or {}
            merged_meta = {**meta1, **meta2}
            thinking1 = str(meta1.get('thinking') or '').strip()
            thinking2 = str(meta2.get('thinking') or '').strip()
            if thinking1 and thinking2:
                merged_meta['thinking'] = thinking1 + "\n\n(continued)\n\n" + thinking2
            elif thinking1:
                merged_meta['thinking'] = thinking1
            merged_meta.pop('stopped', None)  # no longer stopped after continue
            merged_meta.pop('thinking_interrupted', None)

            # Update first message, remove second
            if isinstance(msg1, ChatMessage):
                msg1.content = merged_content
                msg1.metadata = merged_meta
            else:
                msg1['content'] = merged_content
                msg1['metadata'] = merged_meta

            # Also remove the hidden "continue" user message between them if present
            # It's the message at idx2-1 if it's a user message with continue text
            remove_indices = [idx2, idx1 + 1]

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
                    (db_idx1, db1), (db_idx2, db2) = ai_db[-2], ai_db[-1]
                    if _has_immediate_continue_marker(db_messages, db_idx1, db_idx2):
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

            # Get the source session. keep_count indexes into source.history,
            # so this must go through get_session — reading the cache directly
            # forks an empty transcript out of a metadata-only session after a
            # restart (display pagination no longer hydrates it).
            try:
                source = session_manager.get_session(session_id)
            except KeyError:
                raise HTTPException(404, "Session not found")
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
                endpoint_id=getattr(source, 'endpoint_id', None),
            )

            # Copy messages up to keep_count
            msgs_to_copy = source.history[:keep_count]
            for msg in msgs_to_copy:
                # Copy the metadata dict. Sharing it would let the fork's
                # persistence (add_message -> _persist_message stamps
                # _db_id/timestamp onto the dict) mutate the SOURCE session's
                # in-memory messages, corrupting their _db_id and breaking
                # edit/delete-by-id on the original conversation.
                meta = dict(msg.metadata) if isinstance(msg.metadata, dict) else None
                new_session.add_message(ChatMessage(msg.role, msg.content, meta))
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

    @router.get("/api/session/{session_id}/context")
    async def get_session_context_usage(request: Request, session_id: str) -> Dict[str, Any]:
        """Return an estimated whole-chat context usage for the session's model.

        Streaming footers report the prompt size for the last request. This
        endpoint estimates the persisted session context so the header can show
        when the whole chat is approaching compaction.
        """
        _verify_session_owner(request, session_id)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        try:
            from src.context_compactor import auto_compact_threshold_percent
            from src.model_context import estimate_tokens, get_context_length
            from src.model_profiles import supports_user_thinking_toggle

            messages = session.get_context_messages()
            used = int(estimate_tokens(messages))
            ctx_len = int(get_context_length(session.endpoint_url, session.model) or 0)
            thinking_supported = supports_user_thinking_toggle(session.model)
            pct = round((used / ctx_len) * 100, 1) if ctx_len else 0.0
            pct = max(0.0, min(100.0, pct))
            auto_threshold = auto_compact_threshold_percent()
            visible_messages = sum(
                1 for m in session.history
                if not (getattr(m, "metadata", None) or {}).get("hidden")
            )
            compacted_messages = sum(
                1 for m in session.history
                if (getattr(m, "metadata", None) or {}).get("compacted")
            )
            can_compact = used > 0
            return {
                "session_id": session_id,
                "model": session.model,
                "endpoint_url": session.endpoint_url,
                "endpoint_id": getattr(session, "endpoint_id", None),
                "used_tokens": used,
                "context_length": ctx_len,
                "context_percent": pct,
                "messages": visible_messages,
                "context_messages": len(messages),
                "compacted_messages": compacted_messages,
                "can_compact": can_compact,
                "should_compact": pct >= auto_threshold,
                "auto_compact_threshold": auto_threshold,
                "memory_extraction_enabled": getattr(session, "memory_extraction_enabled", True) is not False,
                "memory_injection_enabled": getattr(session, "memory_injection_enabled", True) is not False,
                "skill_injection_enabled": getattr(session, "skill_injection_enabled", True) is not False,
                "thinking_mode": (getattr(session, "thinking_mode", "") or "off") if thinking_supported else "off",
                "thinking_supported": thinking_supported,
                "temperature_override": getattr(session, "temperature_override", None),
                "max_tokens_override": getattr(session, "max_tokens_override", None),
            }
        except Exception as e:
            logger.error(f"Context usage error {session_id}: {e}")
            raise HTTPException(500, str(e))

    @router.post("/api/session/{session_id}/memory-extraction")
    async def set_session_memory_extraction(request: Request, session_id: str) -> Dict[str, Any]:
        """Toggle automatic memory extraction for one chat session."""
        _verify_session_owner(request, session_id)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        try:
            body = await request.json()
        except Exception:
            body = {}
        if "enabled" not in body:
            raise HTTPException(400, "Missing enabled")
        enabled = bool(body.get("enabled"))

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if not db_session:
                raise HTTPException(404, "Session not found")
            db_session.memory_extraction_enabled = enabled
            db.commit()
            session.memory_extraction_enabled = enabled
            return {"status": "success", "memory_extraction_enabled": enabled}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Memory extraction toggle error {session_id}: {e}")
            raise HTTPException(500, "Failed to update memory extraction")
        finally:
            db.close()

    @router.post("/api/session/{session_id}/skill-injection")
    async def set_session_skill_injection(request: Request, session_id: str) -> Dict[str, Any]:
        """Toggle skill injection for one chat session."""
        _verify_session_owner(request, session_id, session_manager)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        try:
            body = await request.json()
        except Exception:
            body = {}
        if "enabled" not in body:
            raise HTTPException(400, "Missing enabled")
        enabled = bool(body.get("enabled"))

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if not db_session:
                # Some active chats exist only in the in-memory manager until
                # their first persisted write. Keep the toggle usable there.
                session.skill_injection_enabled = enabled
                session_manager.save_sessions()
                return {"status": "success", "skill_injection_enabled": enabled}
            db_session.skill_injection_enabled = enabled
            db.commit()
            session.skill_injection_enabled = enabled
            return {"status": "success", "skill_injection_enabled": enabled}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Skill injection toggle error {session_id}: {e}")
            raise HTTPException(500, "Failed to update skill injection")
        finally:
            db.close()

    @router.post("/api/session/{session_id}/memory-injection")
    async def set_session_memory_injection(request: Request, session_id: str) -> Dict[str, Any]:
        """Toggle saved-memory injection for one chat session."""
        _verify_session_owner(request, session_id, session_manager)
        try:
            session = session_manager.get_session(session_id)
        except KeyError:
            raise HTTPException(404, "Session not found")

        try:
            body = await request.json()
        except Exception:
            body = {}
        if "enabled" not in body:
            raise HTTPException(400, "Missing enabled")
        enabled = bool(body.get("enabled"))

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if not db_session:
                session.memory_injection_enabled = enabled
                session_manager.save_sessions()
                return {"status": "success", "memory_injection_enabled": enabled}
            db_session.memory_injection_enabled = enabled
            db.commit()
            session.memory_injection_enabled = enabled
            return {"status": "success", "memory_injection_enabled": enabled}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Memory injection toggle error {session_id}: {e}")
            raise HTTPException(500, "Failed to update memory injection")
        finally:
            db.close()

    @router.post("/api/session/{session_id}/generation-settings")
    async def set_session_generation_settings(request: Request, session_id: str) -> Dict[str, Any]:
        _verify_session_owner(request, session_id, session_manager)
        try:
            session = session_manager.get_session(session_id)
            body = await request.json()
        except KeyError:
            raise HTTPException(404, "Session not found")
        mode = getattr(session, "thinking_mode", "off") or "off"
        raw_effort = body.get("reasoning_effort")
        if raw_effort is not None:
            clean_effort = str(raw_effort).strip().lower()
            if clean_effort in {"", "default"}:
                mode = "off"
            else:
                from src.chatgpt_subscription import get_chatgpt_model_metadata
                meta = get_chatgpt_model_metadata(session.model)
                if meta and clean_effort in [lvl.lower() for lvl in meta.get("supported_reasoning_levels", [])]:
                    mode = f"effort:{clean_effort}"
                else:
                    mode = "off"
        elif "thinking_mode" in body:
            raw_mode = str(body.get("thinking_mode") or "").strip().lower()
            if raw_mode.startswith("effort:"):
                clean_effort = raw_mode[7:].strip()
                from src.chatgpt_subscription import get_chatgpt_model_metadata
                meta = get_chatgpt_model_metadata(session.model)
                if meta and clean_effort in [lvl.lower() for lvl in meta.get("supported_reasoning_levels", [])]:
                    mode = f"effort:{clean_effort}"
                else:
                    mode = "off"
            elif raw_mode in {"", "on", "off"}:
                mode = raw_mode
                from src.model_profiles import supports_user_thinking_toggle
                if not supports_user_thinking_toggle(session.model):
                    mode = "off"
            else:
                raise HTTPException(400, "Invalid thinking mode")

        if "temperature_override" in body:
            temperature = body.get("temperature_override")
            temperature = None if temperature in (None, "") else max(0.0, min(2.0, float(temperature)))
        else:
            temperature = getattr(session, "temperature_override", None)

        if "max_tokens_override" in body:
            max_tokens = body.get("max_tokens_override")
            max_tokens = None if max_tokens in (None, "", 0) else max(256, min(32768, int(max_tokens)))
        else:
            max_tokens = getattr(session, "max_tokens_override", None)

        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == session_id).first()
            if not row:
                raise HTTPException(404, "Session not found")
            row.thinking_mode, row.temperature_override, row.max_tokens_override = mode, temperature, max_tokens
            db.commit()
            session.thinking_mode, session.temperature_override, session.max_tokens_override = mode, temperature, max_tokens
            resp_effort = mode[7:] if mode.startswith("effort:") else ("default" if mode in {"", "off"} else None)
            return {
                "status": "success",
                "thinking_mode": mode,
                "reasoning_effort": resp_effort,
                "temperature_override": temperature,
                "max_tokens_override": max_tokens,
            }
        finally:
            db.close()

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

            from src.context_compactor import SELF_SUMMARY_SYSTEM_PROMPT, normalize_compaction_summary
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
            summary = normalize_compaction_summary(summary)

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
