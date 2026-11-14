# core/session_manager.py
"""
Session management — all session business logic and DB operations.

This is the single place that handles:
- Loading/saving sessions to database
- Adding messages to sessions
- Session lifecycle (create, archive, delete)
"""

import asyncio
import json
import os
import threading
import time
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from .database import Session as DbSession, ChatMessage as DbChatMessage, Document as DbDocument, SessionLocal, utcnow_naive
from .models import Session, ChatMessage
from src.attachment_refs import persistable_message_content
from src.upload_handler import reserve_message_upload_references

# Re-export singleton accessors from models for convenience
from .models import set_session_manager_instance, get_session_manager_instance

logger = logging.getLogger(__name__)


def _message_timestamp_iso(value: Optional[datetime]) -> Optional[str]:
    """Return a stable ISO timestamp for chat message metadata."""
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _parse_msg_content(raw):
    """Parse message content from DB — deserialises JSON arrays back to lists
    (multimodal content with image/audio attachments)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.startswith('[{') and '"type"' in raw:
        try:
            parsed = json.loads(raw)
            # Only treat as serialized multimodal content when EVERY element is
            # a dict whose "type" is a recognized content-block kind. Otherwise a
            # plain text message that merely *looks* like a JSON array of objects
            # (e.g. a user pasting an API schema/sample with a "type" field) was
            # silently parsed back into a list, destroying the original string.
            _BLOCK_TYPES = {
                "text", "image", "image_url", "audio", "input_audio",
                "input_image", "document", "file",
            }
            if (isinstance(parsed, list) and parsed
                    and all(isinstance(p, dict) and p.get("type") in _BLOCK_TYPES
                            for p in parsed)):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return raw


class SessionManager:
    """
    Manages chat sessions with database persistence.

    Usage:
        manager = SessionManager()
        session = manager.create_session(id, name, url, model)
        manager.add_message(session.id, ChatMessage("user", "hello"))
        session = manager.get_session(session_id)
    """

    def __init__(self, sessions_file: str = None):
        # sessions_file kept for backward compat, not used
        self.sessions: Dict[str, Session] = {}
        self.upload_handler = None
        self.load_sessions()
        try:
            self.recover_spooled()
        except Exception as exc:                                 # recovery must never stop the server from starting
            logger.error("Unsaved-message recovery failed: %s", exc)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_sessions(self):
        """Load recent session METADATA from the database — messages are
        hydrated on demand by `get_session`. Previously this walked every
        message of every session into RAM at boot, which on a long-running
        personal-server box could be tens of thousands of rows held forever
        in `self.sessions`.
        """
        db = SessionLocal()
        try:
            db_sessions = db.query(DbSession).filter(
                DbSession.archived == False,
                DbSession.messages.any(),
            ).order_by(DbSession.last_accessed.desc()).limit(100).all()

            # message_count is derived metadata and can drift after interrupted
            # or legacy writes. Count only the bounded discovery set so startup
            # remains metadata-only while lazy hydration sees an authoritative
            # positive count for every discovered non-empty session.
            message_counts = {}
            if db_sessions:
                message_counts = dict(
                    db.query(DbChatMessage.session_id, func.count(DbChatMessage.id))
                    .filter(DbChatMessage.session_id.in_([row.id for row in db_sessions]))
                    .group_by(DbChatMessage.session_id)
                    .all()
                )

            loaded_count = 0
            for db_session in db_sessions:
                try:
                    session = self._db_to_session_meta(db_session)
                    if session is not None:
                        session.message_count = message_counts[db_session.id]
                        self.sessions[db_session.id] = session
                        loaded_count += 1
                except Exception as e:
                    logger.error(f"Error loading session {db_session.id}: {e}")
                    continue

            logger.info(f"Loaded {loaded_count} session(s) (metadata only)")

        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            self.sessions = {}
        finally:
            db.close()

    def _db_to_session_meta(self, db_session: DbSession) -> Optional[Session]:
        """Build a Session with empty history. `get_session` will hydrate
        messages from the DB on first read."""
        headers = db_session.headers
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {}
        session = Session(
            id=db_session.id,
            name=db_session.name,
            endpoint_url=db_session.endpoint_url,
            model=db_session.model,
            rag=db_session.rag,
            archived=db_session.archived,
            headers=headers,
            history=[],
            owner=getattr(db_session, "owner", None),
            is_important=getattr(db_session, "is_important", False) or False,
            memory_extraction_enabled=getattr(db_session, "memory_extraction_enabled", True) is not False,
            memory_injection_enabled=getattr(db_session, "memory_injection_enabled", True) is not False,
            skill_injection_enabled=getattr(db_session, "skill_injection_enabled", True) is not False,
            thinking_mode=getattr(db_session, "thinking_mode", "") or "off",
            temperature_override=getattr(db_session, "temperature_override", None),
            max_tokens_override=getattr(db_session, "max_tokens_override", None),
            cwd=getattr(db_session, "cwd", None) or None,
            endpoint_id=getattr(db_session, "endpoint_id", None) or None,
        )
        session.message_count = getattr(db_session, "message_count", 0) or 0
        return session

    def _db_to_session(self, db_session: DbSession, db) -> Optional[Session]:
        """Convert a database session to a Session object."""
        history = []

        # Try relationship first, then direct query
        if db_session.messages:
            for db_msg in db_session.messages:
                meta = json.loads(db_msg.meta_data) if db_msg.meta_data else {}
                if meta is None: meta = {}
                meta['_db_id'] = db_msg.id
                meta.setdefault('timestamp', _message_timestamp_iso(db_msg.timestamp))
                history.append(ChatMessage(
                    role=db_msg.role,
                    content=_parse_msg_content(db_msg.content),
                    metadata=meta,
                ))
        else:
            db_messages = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == db_session.id
            ).order_by(DbChatMessage.timestamp).all()

            for db_msg in db_messages:
                meta = json.loads(db_msg.meta_data) if db_msg.meta_data else {}
                if meta is None: meta = {}
                meta['_db_id'] = db_msg.id
                meta.setdefault('timestamp', _message_timestamp_iso(db_msg.timestamp))
                history.append(ChatMessage(
                    role=db_msg.role,
                    content=_parse_msg_content(db_msg.content),
                    metadata=meta,
                ))

        if not history:
            return None

        # Parse headers
        headers = db_session.headers
        if isinstance(headers, str):
            try:
                headers = json.loads(headers)
            except json.JSONDecodeError:
                headers = {}

        session = Session(
            id=db_session.id,
            name=db_session.name,
            endpoint_url=db_session.endpoint_url,
            model=db_session.model,
            rag=db_session.rag,
            archived=db_session.archived,
            headers=headers,
            history=history,
            owner=getattr(db_session, 'owner', None),
            is_important=getattr(db_session, 'is_important', False) or False,
            memory_extraction_enabled=getattr(db_session, 'memory_extraction_enabled', True) is not False,
            memory_injection_enabled=getattr(db_session, 'memory_injection_enabled', True) is not False,
            skill_injection_enabled=getattr(db_session, 'skill_injection_enabled', True) is not False,
            thinking_mode=getattr(db_session, "thinking_mode", "") or "off",
            temperature_override=getattr(db_session, "temperature_override", None),
            max_tokens_override=getattr(db_session, "max_tokens_override", None),
            cwd=getattr(db_session, "cwd", None) or None,
            endpoint_id=getattr(db_session, "endpoint_id", None) or None,
        )

        # The rows just loaded are the whole transcript, so they — not the
        # denormalized sessions.message_count column — are the truth for this
        # cached object. get_session's hydration gate compares against this
        # number; seeding it from a drifted column would ask for a reload that
        # can never close the gap.
        session.message_count = len(history)
        return session

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, message: ChatMessage):
        """
        Add a message to a session and persist to database.

        Updates the authoritative history list and persists through this
        manager directly so tests and temporary managers do not depend on the
        process-wide session-manager singleton.

        Args:
            session_id: Session ID
            message: ChatMessage to add
        """
        session = self.get_session(session_id)
        session.history.append(message)
        session._history = session.history
        session.message_count = len(session.history)

        self._persist_message(session_id, message)

    # A failed save is never silent: the message stays in memory (the live conversation keeps working), is flagged, and is re-saved in order on the next
    # successful save for that session. Retries cover brief lock contention only; they block the caller for at most ~0.2 s.
    _PERSIST_RETRY_DELAYS = (0.05, 0.15)
    _UNSAVED_MAX = 200
    # A message whose save failed is also appended to a small on-disk spool, so a process exit cannot lose it. Bounded per session and in total; when the
    # bound is reached the message stays in memory only and that is logged loudly (never silently dropped).
    _SPOOL_MAX_PER_SESSION = 500
    _SPOOL_MAX_BYTES = 32 * 1024 * 1024
    _spool_lock = threading.Lock()

    @staticmethod
    def _spool_dir() -> str:
        from .constants import DATA_DIR
        return os.path.join(os.getenv('ODYSSEUS_DATA_DIR') or DATA_DIR, 'unsaved-messages')

    def _spool_path(self, session_id: str) -> str:
        safe = ''.join(c for c in session_id if c.isalnum() or c in '-_')[:120] or 'session'
        return os.path.join(self._spool_dir(), safe + '.jsonl')

    def _spool_add(self, session_id: str, message: ChatMessage, msg_time) -> bool:
        """Append the unsaved message to the spool (flushed to disk). True when it is durably recorded."""
        try:
            if message.metadata is None:
                message.metadata = {}
            spool_id = message.metadata.setdefault('_spool_id', uuid.uuid4().hex)
            meta = {k: v for k, v in message.metadata.items() if k not in ('_persist_failed', '_db_id')}
            record = {'sid': session_id, 'spool_id': spool_id, 'role': message.role, 'content': persistable_message_content(message.content, message.metadata),
                      'metadata': meta, 'ts': msg_time.isoformat()}
            line = json.dumps(record, default=str, ensure_ascii=False) + '\n'
            with self._spool_lock:
                folder = self._spool_dir()
                os.makedirs(folder, exist_ok=True)
                path = self._spool_path(session_id)
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as handle:
                        existing = handle.read().splitlines()
                    if any(('"%s"' % spool_id) in row for row in existing):
                        return True                              # already spooled (a retry of the same message)
                    if len(existing) >= self._SPOOL_MAX_PER_SESSION:
                        logger.error("Session %s: unsaved-message spool is full (%d); this message is held in memory only", session_id, self._SPOOL_MAX_PER_SESSION)
                        return False
                total = sum(os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder))
                if total + len(line) > self._SPOOL_MAX_BYTES:
                    logger.error("Unsaved-message spool is over its size limit; this message is held in memory only")
                    return False
                with open(path, 'a', encoding='utf-8') as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            return True
        except Exception as exc:
            logger.error("Could not spool an unsaved message for session %s: %s", session_id, exc)
            return False

    def _spool_ack(self, session_id: str, spool_id: str) -> None:
        """The message is in the database: remove it from the spool."""
        try:
            with self._spool_lock:
                path = self._spool_path(session_id)
                if not os.path.exists(path):
                    return
                with open(path, 'r', encoding='utf-8') as handle:
                    rows = handle.read().splitlines()
                keep = [row for row in rows if ('"%s"' % spool_id) not in row]
                if len(keep) == len(rows):
                    return
                if keep:
                    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
                        handle.write('\n'.join(keep) + '\n')
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(path + '.tmp', path)
                else:
                    os.remove(path)
        except Exception as exc:
            logger.warning("Could not update the unsaved-message spool for session %s: %s", session_id, exc)

    def recover_spooled(self) -> dict:
        """After a restart: save every spooled message that is not already in the database, in its original order, then clear it from the spool. Idempotent:
        a message carries its spool id in its stored metadata, so running this twice (or after a partial run) can never create a duplicate."""
        report = {'recovered': 0, 'already_saved': 0, 'failed': 0}
        folder = self._spool_dir()
        if not os.path.isdir(folder):
            return report
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.jsonl'):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    records = [json.loads(row) for row in handle.read().splitlines() if row.strip()]
            except (OSError, ValueError) as exc:
                logger.error("Unreadable unsaved-message spool %s: %s", name, exc)
                report['failed'] += 1
                continue
            for record in sorted(records, key=lambda r: r.get('ts', '')):
                sid, spool_id = record['sid'], record['spool_id']
                db = SessionLocal()
                try:
                    exists = db.query(DbChatMessage.id).filter(DbChatMessage.session_id == sid, DbChatMessage.meta_data.like('%"' + spool_id + '"%')).first()
                finally:
                    db.close()
                if exists:
                    report['already_saved'] += 1
                    self._spool_ack(sid, spool_id)
                    continue
                message = ChatMessage(record['role'], record['content'], dict(record.get('metadata') or {}))
                try:
                    when = datetime.fromisoformat(record['ts'])
                    if self._persist_message_once(sid, message, when):
                        report['recovered'] += 1
                        self._spool_ack(sid, spool_id)
                except Exception as exc:
                    logger.error("Could not recover spooled message %s: %s", spool_id, exc)
                    report['failed'] += 1
        if report['recovered'] or report['failed']:
            logger.warning("Unsaved-message spool recovery: %s", report)
        return report

    @staticmethod
    def _is_transient_db_error(error: Exception) -> bool:
        text = str(error).lower()
        return isinstance(error, OperationalError) or 'database is locked' in text or 'database is busy' in text

    def unsaved_messages(self, session_id: str) -> int:
        """How many messages of this session are in memory but not yet in the database (0 when everything is saved)."""
        return len(getattr(self, '_unsaved', {}).get(session_id, ()))

    def _persist_message(self, session_id: str, message: ChatMessage) -> bool:
        """Persist a single message to the database. Returns True only when it is committed; a failure is logged, flagged on the message and retried later."""
        unsaved = self.__dict__.setdefault('_unsaved', {})
        pending = unsaved.get(session_id)
        if pending:
            # Older unsaved messages go first, with their original timestamps, so the transcript order is preserved.
            for older in list(pending):
                if older[0] is not message and self._persist_message_checked(session_id, older[0], older[1]):
                    pending.remove(older)
        msg_time = datetime.utcnow()
        ok = self._persist_message_checked(session_id, message, msg_time)
        if not ok:
            spooled = self._spool_add(session_id, message, msg_time)
            queue = unsaved.setdefault(session_id, [])
            if not any(item[0] is message for item in queue):
                if len(queue) >= self._UNSAVED_MAX:
                    oldest = queue.pop(0)
                    if (oldest[0].metadata or {}).get('_spool_id'):
                        logger.error("Session %s: unsaved queue of %d full; the oldest message stays in the on-disk spool and is saved at the next restart", session_id, self._UNSAVED_MAX)
                    else:
                        logger.error("Session %s: dropped the oldest unsaved message (queue of %d full, not spooled)", session_id, self._UNSAVED_MAX)
                queue.append((message, msg_time))
            if not spooled:
                message.metadata['_spool_failed'] = True
        elif not unsaved.get(session_id):
            unsaved.pop(session_id, None)
        return ok

    @staticmethod
    def _on_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def _persist_message_checked(self, session_id: str, message: ChatMessage, msg_time) -> bool:
        if (message.metadata or {}).get('_db_id'):
            return True                                         # already committed (idempotent: a retry can never duplicate a row)
        last_error = None
        for attempt in range(len(self._PERSIST_RETRY_DELAYS) + 1):
            try:
                return self._persist_message_once(session_id, message, msg_time)
            except Exception as e:
                last_error = e
                if attempt < len(self._PERSIST_RETRY_DELAYS) and self._is_transient_db_error(e):
                    if not self._on_event_loop():               # never stall the event loop: from a coroutine the next save retries instead
                        time.sleep(self._PERSIST_RETRY_DELAYS[attempt])
                        continue
                break
        logger.error(f"Error persisting message: {last_error}")
        if message.metadata is None:
            message.metadata = {}
        message.metadata['_persist_failed'] = True
        return False

    def _persist_message_once(self, session_id: str, message: ChatMessage, msg_time) -> bool:
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                # A stream/tool callback can outlive a session delete. Do not
                # create a chat_messages row with no parent session; also drop
                # any stale cached session so later writes fail closed too.
                self.sessions.pop(session_id, None)
                self.__dict__.get('_unsaved', {}).pop(session_id, None)
                logger.warning("Dropping message for deleted session %s", session_id)
                return True                                     # nothing to save and nothing to retry: the session is gone

            missing_upload_id = reserve_message_upload_references(
                getattr(self, "upload_handler", None),
                getattr(db_session, "owner", None),
                message.content,
                message.metadata,
            )
            if missing_upload_id:
                raise ValueError(
                    f"Referenced upload is no longer available: {missing_upload_id}"
                )

            msg_id = str(uuid.uuid4())
            if message.metadata is None:
                message.metadata = {}
            message.metadata.setdefault('timestamp', _message_timestamp_iso(msg_time))
            # Multimodal content may contain provider data URLs for the live
            # model call. Persist only readable text plus attachment references
            # so chat_messages/FTS do not duplicate upload bytes.
            _content = persistable_message_content(message.content, message.metadata)
            stored_meta = {k: v for k, v in message.metadata.items() if k not in ('_persist_failed', '_spool_failed')}
            db_message = DbChatMessage(
                id=msg_id,
                session_id=session_id,
                role=message.role,
                content=_content,
                meta_data=json.dumps(stored_meta) if stored_meta else None,
                timestamp=msg_time,
            )
            db.add(db_message)

            if session_id in self.sessions:
                db_session.message_count = len(self.sessions[session_id].history)
            else:
                db_session.message_count = 0
            _now = datetime.now(timezone.utc)
            db_session.last_accessed = _now
            # Clean "last conversation" timestamp — only bumped here on a
            # real message persist, so it powers an accurate "Last active"
            # sort that ignores renames / model swaps / mere opens.
            db_session.last_message_at = _now

            db.commit()

            # Store DB ID on the in-memory message for edit/delete by ID
            message.metadata['_db_id'] = msg_id
            message.metadata.pop('_persist_failed', None)
            message.metadata.pop('_spool_failed', None)
            spool_id = message.metadata.get('_spool_id')
            if spool_id:
                self._spool_ack(session_id, spool_id)

            logger.debug(f"Persisted message to session {session_id}")
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def truncate_messages(self, session_id: str, keep_count: int) -> bool:
        """Truncate session history, keeping only the first `keep_count` messages."""
        session = self.get_session(session_id)

        if keep_count < 0:
            return False

        db = SessionLocal()
        try:
            db_messages = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session_id
            ).order_by(DbChatMessage.timestamp).all()

            deleted = 0
            for msg in db_messages[keep_count:]:
                db.delete(msg)
                deleted += 1

            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                # keep_count can exceed the real message total (e.g. the AI tool
                # defaults to keep_count=10 on a short session); message_count must
                # track the rows that actually remain, not the requested cap.
                db_session.message_count = min(keep_count, len(db_messages))
                db_session.updated_at = datetime.now(timezone.utc)

            db.commit()

            # Update in-memory
            session.history = session.history[:keep_count]
            session._history = session.history

            logger.info(f"Truncated session {session_id} to {keep_count} messages")
            return True

        except Exception as e:
            logger.error(f"Error truncating session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def replace_messages(self, session_id: str, messages: list) -> bool:
        """Replace a session's persisted and in-memory history atomically."""
        session = self.get_session(session_id)
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                logger.warning("Cannot replace history for missing session %s", session_id)
                return False

            # Reserve every incoming attachment before removing any durable
            # message row. reserve_upload() shares the upload lifecycle lock
            # with cleanup, so an upload cannot be deleted between this
            # ownership check/access touch and the replacement transaction.
            # A failed reservation must leave the existing transcript intact.
            for message in messages:
                missing_upload_id = reserve_message_upload_references(
                    getattr(self, "upload_handler", None),
                    getattr(db_session, "owner", None),
                    message.content,
                    message.metadata,
                )
                if missing_upload_id:
                    raise ValueError(
                        f"Referenced upload is no longer available: {missing_upload_id}"
                    )

            db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()
            now = datetime.now(timezone.utc)
            for i, message in enumerate(messages):
                msg_id = str(uuid.uuid4())
                db_message = DbChatMessage(
                    id=msg_id,
                    session_id=session_id,
                    role=message.role,
                    # Mirrors _persist_message: keep raw media bytes out of the
                    # persisted transcript and search index.
                    content=persistable_message_content(message.content, message.metadata),
                    meta_data=json.dumps(message.metadata) if message.metadata else None,
                    timestamp=now + timedelta(microseconds=i),
                )
                db.add(db_message)
                if message.metadata is None:
                    message.metadata = {}
                message.metadata["_db_id"] = msg_id

            db_session.message_count = len(messages)
            db_session.updated_at = now
            db_session.last_accessed = now
            db_session.last_message_at = now

            db.commit()
            session.history = list(messages)
            session._history = session.history
            session.message_count = len(messages)
            logger.info("Replaced session %s history with %d messages", session_id, len(messages))
            return True
        except Exception as e:
            logger.error("Error replacing session history: %s", e)
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID, loading complete DB history when needed.

        Sessions seeded by ``load_sessions`` start with empty history, and a
        cached session can also become partially stale. Refresh metadata first,
        then hydrate whenever the cached transcript is short of the stored rows.
        Model-send routes enter through this method before building context,
        while paginated display history reads SQLite directly.

        The gate compares against ``sync_session_metadata``'s reconciled count
        (the real ``chat_messages`` total), never the denormalized column, so a
        hydrate always closes the gap and the next read is a cache hit.
        """
        if session_id not in self.sessions:
            self._load_session_from_db(session_id)

        # Keep model/endpoint metadata fresh. Endpoint deletion can clear the
        # DB row while a session object is still cached in RAM. Refreshing first
        # also exposes the authoritative message count before completeness is
        # checked.
        self.sync_session_metadata(session_id)

        cached = self.sessions[session_id]
        cached_count = len(cached.history or [])
        stored_count = int(getattr(cached, "message_count", 0) or 0)
        if cached_count < stored_count:
            self._load_session_from_db(session_id)

        # Update last_accessed
        self._touch_session(session_id)

        return self.sessions[session_id]

    def sync_session_metadata(self, session_id: str) -> bool:
        """Refresh non-message session fields from the DB into the cached object.

        ``message_count`` is reconciled against the real ``chat_messages`` rows
        rather than copied from the denormalized ``sessions.message_count``
        column. That column drifts in normal operation — ``_persist_message``
        swallows a failed insert but ``add_message`` has already appended in
        memory, so the next successful persist writes rows+1, and a persist for
        an uncached session writes 0. Hydration keys off this number: a
        drifted-high column would reload the whole transcript on every warm
        read, and a drifted-low one would leave the model a truncated one.
        """
        session = self.sessions.get(session_id)
        if session is None:
            return False
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                return False
            headers = db_session.headers
            if isinstance(headers, str):
                try:
                    headers = json.loads(headers)
                except json.JSONDecodeError:
                    headers = {}
            session.name = db_session.name
            session.endpoint_url = db_session.endpoint_url or ""
            session.endpoint_id = getattr(db_session, "endpoint_id", None) or None
            session.model = db_session.model or ""
            session.headers = headers or {}
            session.rag = db_session.rag
            session.archived = db_session.archived
            session.owner = getattr(db_session, "owner", None)
            session.is_important = getattr(db_session, "is_important", False) or False
            session.cwd = getattr(db_session, "cwd", None) or None
            session.message_count = (
                db.query(DbChatMessage)
                .filter(DbChatMessage.session_id == session_id)
                .count()
            )
            return True
        except Exception as e:
            logger.error(f"Error syncing session metadata {session_id}: {e}")
            return False
        finally:
            db.close()

    def _load_session_from_db(self, session_id: str):
        """Hydrate a single session (with messages) from the database."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session is None:
                raise KeyError(f"Session {session_id} not found")

            session = self._db_to_session(db_session, db)
            if session:
                self.sessions[session_id] = session
            else:
                # No messages — fall back to metadata-only entry so callers
                # don't crash on KeyError for empty sessions.
                meta = self._db_to_session_meta(db_session)
                if meta is None:
                    raise KeyError(f"Session {session_id} could not be loaded")
                self.sessions[session_id] = meta

        except KeyError:
            raise
        except Exception as e:
            logger.error(f"Error loading session {session_id}: {e}")
            raise
        finally:
            db.close()

    def _touch_session(self, session_id: str):
        """Update last_accessed timestamp."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.last_accessed = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.error(f"Error updating last_accessed: {e}")
            db.rollback()
        finally:
            db.close()

    def create_session(
        self,
        session_id: str,
        name: str,
        endpoint_url: str,
        model: str,
        rag: bool = False,
        owner: str = None,
        cwd: str = None,
        headers: Optional[Dict[str, str]] = None,
        endpoint_id: Optional[str] = None,
    ) -> Session:
        """Create a new session and save to database."""
        from src.chatgpt_subscription import is_chatgpt_subscription_base
        session_headers = {} if is_chatgpt_subscription_base(endpoint_url) else dict(headers or {})
        endpoint_id = (endpoint_id or "").strip() or None
        db = SessionLocal()
        try:
            db_session = DbSession(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers=session_headers,
                owner=owner,
                cwd=cwd or None,
                endpoint_id=endpoint_id,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(db_session)
            db.commit()

            session = Session(
                id=session_id,
                name=name,
                endpoint_url=endpoint_url,
                model=model,
                rag=rag,
                headers=session_headers,
                owner=owner,
                cwd=cwd or None,
                endpoint_id=endpoint_id,
            )

            self.sessions[session_id] = session
            return session

        except Exception as e:
            db.rollback()
            logger.error(f"Error creating session: {e}")
            raise
        finally:
            db.close()

    def delete_session(self, session_id: str, *, delete_images: bool = False) -> bool:
        """Permanently delete a session and all its messages."""
        db = SessionLocal()
        try:
            try:
                from src.session_image_cleanup import cleanup_session_images, preserve_session_images
                if delete_images:
                    cleanup_session_images(session_id, db=db)
                else:
                    preserve_session_images(session_id, db=db)
            except Exception as e:
                logger.warning(f"Image cleanup failed while deleting session {session_id}: {e}")

            # Detach documents so they survive as orphans in the library
            db.query(DbDocument).filter(DbDocument.session_id == session_id).update(
                {DbDocument.session_id: None}, synchronize_session=False
            )

            # Delete messages
            db.query(DbChatMessage).filter(DbChatMessage.session_id == session_id).delete()

            # Delete session
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db.delete(db_session)

            # Drop the in-memory copy even when there is no DB row. A "ghost"
            # session lives only here (never persisted, or its row was removed
            # out-of-band); without this it can never be cleared and keeps
            # 404ing on every operation (issue #1044).
            removed_in_memory = self.sessions.pop(session_id, None) is not None

            if db_session or removed_in_memory:
                # Commit the document-detach / message-delete above (a no-op when
                # the ghost had no rows) together with the session delete.
                db.commit()
                logger.info(f"Deleted session {session_id}")
                return True
            return False

        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Session updates
    # ------------------------------------------------------------------

    def update_session_name(self, session_id: str, name: str):
        """Update session name."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.name = name
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].name = name
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating session name: {e}")
            raise
        finally:
            db.close()

    def archive_session(self, session_id: str):
        """Archive a session."""
        if session_id not in self.sessions:
            return

        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.archived = True
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()
                self.sessions[session_id].archived = True
        except Exception as e:
            db.rollback()
            logger.error(f"Error archiving session: {e}")
            raise
        finally:
            db.close()

    def mark_important(self, session_id: str, important: bool = True):
        """Mark session as important."""
        db = SessionLocal()
        try:
            db_session = db.query(DbSession).filter(DbSession.id == session_id).first()
            if db_session:
                db_session.is_important = important
                db_session.updated_at = datetime.now(timezone.utc)
                db.commit()

                if session_id in self.sessions:
                    self.sessions[session_id].is_important = important
            else:
                raise KeyError(f"Session {session_id} not found")
        except Exception as e:
            db.rollback()
            logger.error(f"Error marking session important: {e}")
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_sessions_for_user(self, username: Optional[str] = None) -> Dict[str, Session]:
        """Return sessions for a specific user (or all if username is None)."""
        if username is None:
            return self.sessions
        return {
            sid: s for sid, s in self.sessions.items()
            if s.owner == username
        }

    def save_sessions(self):
        """No-op for DB compatibility."""

    def ensure_task_session(self, session_id: str, name: str, endpoint_url: str, model: str, owner: str = None, task: object = None) -> Session:
        """Create a task session if it doesn't exist, or return the existing one.

        Unlike create_session, this checks the cache first and does NOT
        overwrite an existing in-memory session. The task scheduler must
        use this instead of direct dict assignment.
        """
        if session_id in self.sessions:
            return self.sessions[session_id]

        session = self.create_session(session_id, name, endpoint_url, model, owner=owner)
        if task is not None:
            task.session_id = session_id
        return session

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_empty_sessions(self, auto_archive_days: int = 30, min_age_hours: int = 1) -> dict:
        """Clean up empty and old sessions.

        Args:
            auto_archive_days: Age in days before non-important sessions are archived.
            min_age_hours: Minimum age in hours before an empty session can be deleted.
                          Prevents deleting sessions that were just created.
        """
        db = SessionLocal()
        stats = {'deleted_empty': 0, 'archived_old': 0, 'total_checked': 0}

        try:
            all_sessions = db.query(DbSession).all()
            cutoff_date = utcnow_naive() - timedelta(days=auto_archive_days)
            min_age = utcnow_naive() - timedelta(hours=min_age_hours)

            for db_session in all_sessions:
                stats['total_checked'] += 1

                # Delete empty sessions only if older than min_age_hours
                if db_session.message_count == 0:
                    if db_session.created_at is not None:
                        created = db_session.created_at
                        if created.tzinfo is None:
                            created = created.replace(tzinfo=timezone.utc)
                        if created > min_age:
                            continue  # Too young to delete
                    if db_session.id in self.sessions:
                        del self.sessions[db_session.id]
                    db.delete(db_session)
                    stats['deleted_empty'] += 1

                # Archive old sessions
                elif (not db_session.archived and
                      db_session.last_accessed and
                      db_session.last_accessed < cutoff_date and
                      db_session.message_count > 0 and
                      not getattr(db_session, 'is_important', False)):
                    db_session.archived = True
                    stats['archived_old'] += 1

            db.commit()
            logger.info(f"Cleanup: {stats['deleted_empty']} deleted, {stats['archived_old']} archived")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            db.rollback()
            raise
        finally:
            db.close()

        return stats
