"""Reconciliation-first OpenHands run projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

AGENT_RUN_PROJECTION_UNIQUE_CONSTRAINT = ("source_system", "source_event_id")
_KNOWN_KINDS = {
    "MessageEvent",
    "ActionEvent",
    "ObservationEvent",
    "ConversationStateUpdate",
    "RunStatus",
}

_TERMINAL = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class SourceEvent:
    source_system: str
    source_event_id: str
    kind: str
    conversation_id: str
    execution_id: str | None
    parent_id: str | None
    source_order: int | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProjectedEvent:
    source_system: str
    source_event_id: str
    kind: str
    conversation_id: str
    execution_id: str | None
    parent_id: str | None
    source_order: int | None
    arrival_sequence: int
    payload: dict[str, Any]
    quarantined: bool = False


@dataclass(frozen=True)
class AgentRunProjection:
    conversation_id: str
    execution_id: str | None
    status: str
    conversation_status: str
    automation_status: str | None
    local_status: str
    stale: bool
    degraded: bool
    events: tuple[ProjectedEvent, ...]
    reconciliation_position: int


class ProjectionStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ProjectedEvent] = {}
        self._arrival = 0
        self._stale = False
        self._degraded = False
        self._conversation_id: str | None = None
        self._execution_id: str | None = None
        self._conversation_status = "unknown"
        self._automation_status: str | None = None
        self._local_status = "synchronizing"
        self._position = 0

    def _identity(self, event: SourceEvent) -> tuple[str, str]:
        system = "agent-server" if event.source_system == "websocket" else event.source_system
        return (system, event.source_event_id)

    def apply(self, event: SourceEvent) -> ProjectedEvent | None:
        key = self._identity(event)
        self._conversation_id = event.conversation_id
        if event.execution_id:
            self._execution_id = event.execution_id
        quarantined = event.kind not in _KNOWN_KINDS
        if quarantined:
            self._degraded = True
        if key in self._rows:
            return None
        self._arrival += 1
        row = ProjectedEvent(
            source_system=key[0],
            source_event_id=event.source_event_id,
            kind=event.kind,
            conversation_id=event.conversation_id,
            execution_id=event.execution_id,
            parent_id=event.parent_id,
            source_order=event.source_order,
            arrival_sequence=self._arrival,
            payload=dict(event.payload),
            quarantined=quarantined,
        )
        self._rows[key] = row
        status = str((event.payload or {}).get("status") or "")
        if event.kind == "ConversationStateUpdate" and status:
            self._conversation_status = status
        if event.source_system == "automation" and status:
            self._automation_status = status
        if status in _TERMINAL:
            self._local_status = status
        elif self._local_status == "synchronizing":
            self._local_status = "running"
        return row

    def mark_unreadable(self) -> None:
        self._stale = True
        self._degraded = True

    def rows(self) -> list[ProjectedEvent]:
        return sorted(self._rows.values(), key=lambda row: (row.source_order or 0, row.arrival_sequence))

    def projection(self, conversation_id: str) -> AgentRunProjection:
        status = self._conversation_status if self._conversation_status in _TERMINAL else (
            self._local_status if self._local_status in _TERMINAL else self._conversation_status
            if self._conversation_status != "unknown"
            else self._local_status
        )
        if self._conversation_status in _TERMINAL:
            status = self._conversation_status
        return AgentRunProjection(
            conversation_id=conversation_id,
            execution_id=self._execution_id,
            status=status,
            conversation_status=self._conversation_status,
            automation_status=self._automation_status,
            local_status=self._local_status,
            stale=self._stale,
            degraded=self._degraded,
            events=tuple(self.rows()),
            reconciliation_position=self._position,
        )


class ProjectionReconciler:
    def __init__(self, store: ProjectionStore) -> None:
        self.store = store

    def ingest(self, events: Iterable[SourceEvent | None]) -> None:
        self._apply_all(events)

    def reconcile(self, events: Iterable[SourceEvent | None]) -> None:
        inserted = self._apply_all(events)
        if inserted:
            self.store._position += 1

    def _apply_all(self, events: Iterable[SourceEvent | None]) -> int:
        inserted = 0
        for event in events:
            if event is None:
                self.store.mark_unreadable()
                continue
            if self.store.apply(event) is not None:
                inserted += 1
        return inserted

    def snapshot(self) -> AgentRunProjection:
        conversation_id = self.store._conversation_id or ""
        return self.store.projection(conversation_id)
