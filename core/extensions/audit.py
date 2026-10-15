"""audit.py — append-only audit log for OXS extension lifecycle events.

The audit log has two coordinated stores:

  1. ``OXS_AUDIT_LOG`` (file) — JSON Lines, append-only, never edited in
     place. Survives even when the DB is wiped. One line per event. This is
     the canonical source for compliance/forensics.

  2. ``extension_audits`` (DB table) — denormalized mirror for queryable
     admin UI. Mirrors file events on every successful write. Both writers
     share a lock so events land in both stores atomically.

Concurrency model: each call takes ``_AUDIT_LOCK`` briefly. We do not queue
events across processes — if the operator runs multiple Odysseus workers,
each writes independently and the file ends up interleaved (fine because
JSON Lines is append-tolerant).

Failure mode: if the DB write fails, the file write still happens. If the
file write fails, the DB write is rolled back and the exception propagates.
Operators see DB errors via normal logging; file errors raise so callers
can decide.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.constants import OXS_AUDIT_LOG


_AUDIT_LOCK = threading.Lock()


# ── Event shape ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditEvent:
    """One audit event.

    ``event`` is a dotted identifier of the form ``<category>.<action>``,
    e.g. ``extension.install``, ``extension.enable``, ``extension.hook.failed``,
    ``extension.capability.violation``. Categories are not enforced by the
    schema; callers choose the right one.

    ``extension_id`` is None for system-level events (e.g. ``oxs.startup``,
    ``oxs.rescan``). ``actor`` identifies who triggered the event:
    ``"system"``, an operator username, or ``"extension:<id>"`` for events
    raised by an extension during execution.
    """
    event: str
    actor: str
    extension_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: _utcnow_iso())

    def to_json_line(self) -> str:
        """Serialize as a single JSON Lines line, no trailing newline."""
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── Sink abstraction ───────────────────────────────────────────────────────


class AuditSink:
    """Coordinates file + DB writes behind one call.

    The DB mirror is optional (set ``db_session_factory`` to None to disable).
    When enabled, every event is mirrored to ``extension_audits``. The DB
    write happens inside the same lock as the file write so the two stores
    are ordered consistently per thread.

    ``db_session_factory`` is a zero-arg callable that returns a
    sessionmaker (or session-like context manager). We use a callable so
    tests can re-bind ``core.database`` after the sink was constructed.
    """

    def __init__(
        self,
        log_path: str = OXS_AUDIT_LOG,
        db_session_factory=None,
    ):
        self.log_path = log_path
        self._factory = db_session_factory

    def write(self, event: AuditEvent) -> None:
        line = event.to_json_line()
        with _AUDIT_LOCK:
            self._append_file(line)
            self._mirror_db(event)

    def _append_file(self, line: str) -> None:
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        # O_APPEND + O_CREAT gives us atomic appends on POSIX for lines ≤
        # PIPE_BUF (4 KB on Linux). Audit lines are tiny so this holds.
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _mirror_db(self, event: AuditEvent) -> None:
        if self._factory is None:
            return
        try:
            from core.database import ExtensionAudit
            factory = self._factory
            with factory() as db:
                db.add(ExtensionAudit(
                    extension_id=event.extension_id,
                    event=event.event,
                    actor=event.actor,
                    payload=event.payload,
                ))
                db.commit()
        except Exception:
            # DB errors are surfaced by the caller's logging; we do not
            # block the file write. If you want fail-loud here, replace
            # with `raise`.
            import logging
            logging.getLogger(__name__).exception(
                "OXS audit DB mirror failed for event=%s ext=%s",
                event.event, event.extension_id,
            )


# ── Module-level singleton + helpers ───────────────────────────────────────

_DEFAULT_SINK: AuditSink | None = None


def _get_sink() -> AuditSink:
    global _DEFAULT_SINK
    if _DEFAULT_SINK is None:
        # Use a callable so the actual sessionmaker is resolved on every
        # write — not at sink-construction time. This matters in tests
        # where ``core.database`` is stubbed and re-imported between
        # writes.
        def _resolve_factory():
            try:
                from core.database import SessionLocal
                # SessionLocal is a sessionmaker; calling it returns a
                # Session which supports the context manager protocol.
                return SessionLocal()
            except Exception:
                return None

        _DEFAULT_SINK = AuditSink(
            log_path=OXS_AUDIT_LOG,
            db_session_factory=_resolve_factory,
        )
    return _DEFAULT_SINK


def set_sink(sink: AuditSink) -> None:
    """Replace the default sink. Used by app startup to wire in DB mirroring
    after ``core.database`` is importable."""
    global _DEFAULT_SINK
    _DEFAULT_SINK = sink


def audit_log(
    event: str,
    *,
    extension_id: str | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    """Append one event to the audit log.

    Cheap; safe to use from any code path. Exceptions during file write
    propagate (so callers can decide); exceptions during DB mirror are
    swallowed and logged.
    """
    ev = AuditEvent(
        event=event,
        actor=actor,
        extension_id=extension_id,
        payload=payload or {},
    )
    _get_sink().write(ev)


def audit_query(
    extension_id: str | None = None,
    *,
    limit: int = 100,
    event_prefix: str | None = None,
) -> list[dict]:
    """Read recent events from the DB mirror. Used by the admin UI.

    Returns a list of dicts newest-first. If the DB is unavailable, returns
    an empty list — callers should fall back to :func:`audit_tail`.
    """
    try:
        from core.database import ExtensionAudit, SessionLocal
        with SessionLocal() as db:
            q = db.query(ExtensionAudit)
            if extension_id is not None:
                q = q.filter(ExtensionAudit.extension_id == extension_id)
            if event_prefix:
                q = q.filter(ExtensionAudit.event.like(event_prefix + "%"))
            rows = q.order_by(ExtensionAudit.created_at.desc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "extension_id": r.extension_id,
                    "event": r.event,
                    "actor": r.actor,
                    "payload": r.payload,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
    except Exception:
        import logging
        logging.getLogger(__name__).exception("OXS audit_query failed")
        return []


def audit_tail(n: int = 100) -> list[dict]:
    """Return the last ``n`` events from the file log.

    Reads the whole file but tolerates malformed lines (skips them). Used as
    a fallback when the DB is unavailable. Newest-last; reverse for display.
    """
    if not os.path.exists(OXS_AUDIT_LOG):
        return []
    out: list[dict] = []
    with open(OXS_AUDIT_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed lines — they could be from an older OXS
                # version or a partial write.
                continue
    return out[-n:]


__all__ = [
    "AuditEvent",
    "AuditSink",
    "audit_log",
    "audit_query",
    "audit_tail",
    "set_sink",
]