"""Durable notification event and inbox helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from core.database import (
    NotificationEvent,
    NotificationInboxItem,
    SessionLocal,
    utcnow_naive,
)

SYSTEM_EVENT = "system_event"
INBOX_RECORD = "inbox_record"
ACTIONABLE = "actionable"

EVENT_CLASSES = {SYSTEM_EVENT, INBOX_RECORD, ACTIONABLE}
INBOX_CLASSES = {INBOX_RECORD, ACTIONABLE}
SEVERITIES = {"info", "attention", "urgent", "error"}


def _normalize_owner(owner: str | None) -> str | None:
    owner = (owner or "").strip()
    return owner or None


def _clamp_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _safe_limit(limit: int | None, default: int = 50, maximum: int = 200) -> int:
    try:
        n = int(limit or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(maximum, n))


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _owner_query(query, model, owner: str | None):
    owner = _normalize_owner(owner)
    if owner is None:
        return query.filter(model.owner == None)  # noqa: E711
    return query.filter(model.owner == owner)


def _validate_event_class(event_class: str) -> str:
    value = (event_class or SYSTEM_EVENT).strip()
    if value not in EVENT_CLASSES:
        raise ValueError(f"Unknown notification event_class: {value}")
    return value


def _validate_severity(severity: str) -> str:
    value = (severity or "info").strip()
    if value not in SEVERITIES:
        raise ValueError(f"Unknown notification severity: {value}")
    return value


def _serialize_event(event: NotificationEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "owner": event.owner,
        "event_class": event.event_class,
        "title": event.title,
        "body": event.body,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "source_url": event.source_url,
        "severity": event.severity,
        "category": event.category,
        "dedupe_key": event.dedupe_key,
        "metadata": event.metadata_json or {},
        "retention_expires_at": _iso(event.retention_expires_at),
        "created_at": _iso(event.created_at),
    }


def _serialize_item(item: NotificationInboxItem) -> dict[str, Any]:
    event = item.event
    return {
        "id": item.id,
        "owner": item.owner,
        "event_id": item.event_id,
        "notification_kind": item.notification_kind,
        "primary_action": item.primary_action,
        "action_url": item.action_url,
        "is_read": bool(item.is_read),
        "read_at": _iso(item.read_at),
        "dismissed_at": _iso(item.dismissed_at),
        "archived_at": _iso(item.archived_at),
        "created_at": _iso(item.created_at),
        "event": _serialize_event(event) if event else None,
        "event_class": event.event_class if event else None,
        "title": event.title if event else "",
        "body": event.body if event else None,
        "source_type": event.source_type if event else None,
        "source_id": event.source_id if event else None,
        "source_url": event.source_url if event else None,
        "severity": event.severity if event else "info",
        "category": event.category if event else None,
        "metadata": event.metadata_json if event and event.metadata_json else {},
    }


def _upsert_event(
    db: OrmSession,
    *,
    owner: str | None,
    event_class: str,
    title: str,
    body: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    severity: str = "info",
    category: str | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
    retention_expires_at: datetime | None = None,
) -> NotificationEvent:
    owner = _normalize_owner(owner)
    event_class = _validate_event_class(event_class)
    severity = _validate_severity(severity)
    event = None
    if dedupe_key:
        event = _owner_query(db.query(NotificationEvent), NotificationEvent, owner).filter(
            NotificationEvent.dedupe_key == dedupe_key
        ).first()
    if event is None:
        event = NotificationEvent(
            id=str(uuid.uuid4()),
            owner=owner,
            dedupe_key=dedupe_key,
            created_at=utcnow_naive(),
        )
        db.add(event)

    event.event_class = event_class
    event.title = _clamp_text(title or "Notification", 240) or "Notification"
    event.body = _clamp_text(body, 20000)
    event.source_type = _clamp_text(source_type, 80)
    event.source_id = _clamp_text(source_id, 240)
    event.source_url = _clamp_text(source_url, 2000)
    event.severity = severity
    event.category = _clamp_text(category, 80)
    event.metadata_json = metadata or {}
    event.retention_expires_at = retention_expires_at
    return event


def record_notification_event(
    *,
    owner: str | None,
    event_class: str = SYSTEM_EVENT,
    title: str,
    body: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    severity: str = "info",
    category: str | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
    retention_expires_at: datetime | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        event = _upsert_event(
            db,
            owner=owner,
            event_class=event_class,
            title=title,
            body=body,
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            severity=severity,
            category=category,
            dedupe_key=dedupe_key,
            metadata=metadata,
            retention_expires_at=retention_expires_at,
        )
        db.commit()
        db.refresh(event)
        return _serialize_event(event)
    finally:
        db.close()


def create_inbox_notification(
    *,
    owner: str | None,
    notification_kind: str = INBOX_RECORD,
    title: str,
    body: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
    severity: str = "info",
    category: str | None = None,
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
    primary_action: str | None = None,
    action_url: str | None = None,
    retention_expires_at: datetime | None = None,
) -> dict[str, Any]:
    if notification_kind not in INBOX_CLASSES:
        raise ValueError(f"Unknown inbox notification_kind: {notification_kind}")
    db = SessionLocal()
    try:
        event = _upsert_event(
            db,
            owner=owner,
            event_class=notification_kind,
            title=title,
            body=body,
            source_type=source_type,
            source_id=source_id,
            source_url=source_url,
            severity=severity,
            category=category,
            dedupe_key=dedupe_key,
            metadata=metadata,
            retention_expires_at=retention_expires_at,
        )
        db.flush()
        item = db.query(NotificationInboxItem).filter(
            NotificationInboxItem.event_id == event.id
        ).first()
        if item is None:
            item = NotificationInboxItem(
                id=str(uuid.uuid4()),
                owner=_normalize_owner(owner),
                event_id=event.id,
                notification_kind=notification_kind,
                created_at=utcnow_naive(),
            )
            db.add(item)
        item.notification_kind = notification_kind
        item.primary_action = _clamp_text(primary_action, 80)
        item.action_url = _clamp_text(action_url, 2000)
        db.commit()
        db.refresh(item)
        return _serialize_item(item)
    finally:
        db.close()


def record_task_notification(
    *,
    task_name: str,
    status: str,
    task_id: str | None = None,
    owner: str | None = None,
    body: str | None = None,
    run_id: str | None = None,
    output_target: str | None = None,
) -> dict[str, Any]:
    """Persist the durable counterpart to the transient task notification."""
    clean_status = (status or "").strip().lower() or "unknown"
    ok = clean_status == "success"
    clean_name = (task_name or "Task").strip() or "Task"
    metadata = {
        "task_id": task_id,
        "run_id": run_id,
        "status": clean_status,
        "output_target": output_target or "session",
    }
    source_id = run_id or task_id
    dedupe_key = f"task-run:{run_id}:{clean_status}" if run_id else None

    if ok and body:
        return create_inbox_notification(
            owner=owner,
            notification_kind=INBOX_RECORD,
            title=clean_name,
            body=body,
            source_type="task_run",
            source_id=source_id,
            source_url="#tasks",
            severity="info",
            category="task",
            dedupe_key=dedupe_key,
            metadata=metadata,
            primary_action="open_task",
            action_url=f"odysseus://tasks/{task_id or ''}",
        )

    if not ok:
        return create_inbox_notification(
            owner=owner,
            notification_kind=ACTIONABLE,
            title=f"Task failed: {clean_name}",
            body=body,
            source_type="task_run",
            source_id=source_id,
            source_url="#tasks",
            severity="error",
            category="task",
            dedupe_key=dedupe_key,
            metadata=metadata,
            primary_action="open_task",
            action_url=f"odysseus://tasks/{task_id or ''}",
        )

    return record_notification_event(
        owner=owner,
        event_class=SYSTEM_EVENT,
        title=f"Task finished: {clean_name}",
        body=None,
        source_type="task_run",
        source_id=source_id,
        source_url="#tasks",
        severity="info",
        category="task",
        dedupe_key=dedupe_key,
        metadata=metadata,
    )


def list_inbox_notifications(
    *,
    owner: str | None,
    limit: int = 50,
    include_archived: bool = False,
    include_dismissed: bool = False,
) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = _owner_query(db.query(NotificationInboxItem), NotificationInboxItem, owner)
        if not include_archived:
            q = q.filter(NotificationInboxItem.archived_at == None)  # noqa: E711
        if not include_dismissed:
            q = q.filter(NotificationInboxItem.dismissed_at == None)  # noqa: E711
        items = q.order_by(NotificationInboxItem.created_at.desc()).limit(_safe_limit(limit)).all()
        return [_serialize_item(item) for item in items]
    finally:
        db.close()


def list_notification_events(
    *,
    owner: str | None,
    limit: int = 100,
    event_class: str | None = None,
) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        q = _owner_query(db.query(NotificationEvent), NotificationEvent, owner)
        if event_class:
            q = q.filter(NotificationEvent.event_class == event_class)
        events = q.order_by(NotificationEvent.created_at.desc()).limit(_safe_limit(limit, default=100)).all()
        return [_serialize_event(event) for event in events]
    finally:
        db.close()


def count_unread_notifications(*, owner: str | None) -> int:
    db = SessionLocal()
    try:
        q = _owner_query(db.query(NotificationInboxItem), NotificationInboxItem, owner).filter(
            NotificationInboxItem.is_read == False,  # noqa: E712
            NotificationInboxItem.dismissed_at == None,  # noqa: E711
            NotificationInboxItem.archived_at == None,  # noqa: E711
        )
        return int(q.count())
    finally:
        db.close()


def mark_notification_read(*, item_id: str, owner: str | None, read: bool = True) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        item = _owner_query(db.query(NotificationInboxItem), NotificationInboxItem, owner).filter(
            NotificationInboxItem.id == item_id
        ).first()
        if not item:
            return None
        item.is_read = bool(read)
        item.read_at = utcnow_naive() if read else None
        db.commit()
        db.refresh(item)
        return _serialize_item(item)
    finally:
        db.close()


def dismiss_notification(*, item_id: str, owner: str | None) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        item = _owner_query(db.query(NotificationInboxItem), NotificationInboxItem, owner).filter(
            NotificationInboxItem.id == item_id
        ).first()
        if not item:
            return None
        item.is_read = True
        item.read_at = item.read_at or utcnow_naive()
        item.dismissed_at = utcnow_naive()
        db.commit()
        db.refresh(item)
        return _serialize_item(item)
    finally:
        db.close()


def archive_notification(*, item_id: str, owner: str | None) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        item = _owner_query(db.query(NotificationInboxItem), NotificationInboxItem, owner).filter(
            NotificationInboxItem.id == item_id
        ).first()
        if not item:
            return None
        item.is_read = True
        item.read_at = item.read_at or utcnow_naive()
        item.archived_at = utcnow_naive()
        db.commit()
        db.refresh(item)
        return _serialize_item(item)
    finally:
        db.close()
