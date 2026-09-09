from __future__ import annotations

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.projection import (  # noqa: E402
    AgentRunProjection,
    ProjectionReconciler,
    ProjectionStore,
    SourceEvent,
)


def _event(**overrides):
    payload = {
        "source_system": "agent-server",
        "source_event_id": "evt-1",
        "kind": "MessageEvent",
        "conversation_id": "conv-1",
        "execution_id": "agent-server:conv-1",
        "parent_id": None,
        "source_order": 1,
        "payload": {"text": "hi"},
    }
    payload.update(overrides)
    return SourceEvent(**payload)


def test_reconcile_is_idempotent():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    rest_events = [_event(source_event_id="evt-2", source_order=2), _event()]
    websocket_overlap = [_event(source_system="websocket", source_event_id="evt-1", source_order=1)]
    reconciler.ingest(websocket_overlap)
    reconciler.reconcile(rest_events)
    first = reconciler.snapshot()
    reconciler.reconcile(rest_events)
    assert reconciler.snapshot() == first


def test_duplicates_and_rest_ws_inversion_keep_one_row():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.ingest([_event(source_system="websocket")])
    reconciler.reconcile([_event(), _event()])
    rows = [row for row in reconciler.snapshot().events if row.source_event_id == "evt-1"]
    assert len(rows) == 1


def test_parent_ids_are_retained():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.reconcile([_event(source_event_id="child", parent_id="evt-1", source_order=2)])
    child = next(row for row in reconciler.snapshot().events if row.source_event_id == "child")
    assert child.parent_id == "evt-1"


def test_late_terminal_event_updates_status():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.reconcile([_event(kind="MessageEvent")])
    assert reconciler.snapshot().status != "completed"
    reconciler.reconcile([_event(source_event_id="done", kind="ConversationStateUpdate", payload={"status": "completed"}, source_order=9)])
    assert reconciler.snapshot().status == "completed"


def test_projector_restart_reloads_store():
    store = ProjectionStore()
    first = ProjectionReconciler(store)
    first.reconcile([_event()])
    restarted = ProjectionReconciler(store)
    assert restarted.snapshot() == first.snapshot()


def test_unknown_event_is_quarantined_without_deleting_conversation():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.reconcile([
        _event(),
        _event(source_event_id="mystery", kind="NotARealEvent", source_order=2),
    ])
    snap = reconciler.snapshot()
    assert snap.conversation_id == "conv-1"
    assert any(row.quarantined for row in snap.events if row.source_event_id == "mystery")
    assert snap.degraded is True


def test_unreadable_history_does_not_drop_conversation():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.reconcile([_event()])
    reconciler.reconcile([None, _event(source_event_id="evt-3", source_order=3)])  # type: ignore[list-item]
    snap = reconciler.snapshot()
    assert snap.conversation_id == "conv-1"
    assert snap.stale is True or snap.degraded is True


def test_composed_status_prefers_terminal_owner():
    store = ProjectionStore()
    reconciler = ProjectionReconciler(store)
    reconciler.ingest([
        _event(source_system="automation", source_event_id="auto-1", kind="RunStatus", payload={"status": "running"}),
    ])
    reconciler.reconcile([
        _event(kind="ConversationStateUpdate", payload={"status": "completed"}, source_event_id="done"),
    ])
    snap = reconciler.snapshot()
    assert snap.status == "completed"
    assert snap.automation_status == "running"
    assert snap.conversation_status == "completed"


def test_projection_exposes_unique_constraint():
    from pathlib import Path

    from services.agents.projection import AGENT_RUN_PROJECTION_UNIQUE_CONSTRAINT

    assert AGENT_RUN_PROJECTION_UNIQUE_CONSTRAINT == ("source_system", "source_event_id")
    registered = Path(__file__).resolve().parents[2] / "src" / "database.py"
    assert "AGENT_RUN_PROJECTION_UNIQUE_CONSTRAINT" in registered.read_text(encoding="utf-8")
    store = ProjectionStore()
    store.apply(_event())
    store.apply(_event())
    assert len(store.rows()) == 1
    assert isinstance(store.projection("conv-1"), AgentRunProjection)
