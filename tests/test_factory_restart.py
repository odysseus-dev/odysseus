"""
Regression tests for Factory restart_project (partial + full modes).

Verifies that:
  1. Partial restart resets "running" tasks (Bug 1 fix)
  2. Partial restart promotes non-root pending tasks whose deps are done (Bug 2 fix)
  3. Full restart resets ALL tasks
  4. _promote_eligible_tasks skips blocked tasks with incomplete deps
"""

import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import services.factory_models  # noqa: F401 — registers tables with Base.metadata
from services.factory_service import FactoryService
from services.factory_models import FactoryProject, FactoryNode, FactoryEdge


# Build a separate in-memory engine so we don't mutate the global SessionLocal.
# We monkeypatch cdb.SessionLocal so that factory_service's get_db_session()
# picks up our test engine.
_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _patch_session(monkeypatch):
    """Point core.database.SessionLocal at our test engine so get_db_session()
    in factory_service uses the in-memory database with all factory tables."""
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    monkeypatch.setattr(cdb, "engine", _ENGINE)


@pytest.fixture
def svc():
    return FactoryService()


# ── Helpers ────────────────────────────────────────────────────────────


def _make_project(db) -> int:
    """Create a stub project and return its id."""
    p = FactoryProject(
        title="Test Project",
        description="",
        status="queued",
        owner="default",
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.id


def _make_task(db, project_id: int, title: str, status: str = "pending",
               **kw) -> int:
    """Create a stub node and return its id."""
    n = FactoryNode(
        project_id=project_id,
        title=title,
        status=status,
        created_at=_now(),
        updated_at=_now(),
        **kw,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n.id


def _make_edge(db, project_id: int, from_id: int, to_id: int):
    e = FactoryEdge(
        project_id=project_id,
        from_node_id=from_id,
        to_node_id=to_id,
    )
    db.add(e)
    db.commit()


def _task_status(db, task_id: int) -> str:
    n = db.query(FactoryNode).filter(FactoryNode.id == task_id).first()
    return n.status if n else None


# ── Bug 1: Partial restart resets "running" tasks ──────────────────────


def test_restart_resets_running_tasks(svc):
    """Partial restart must reset tasks that are stuck in 'running' (orphaned
    by a dead orchestrator). After reset, a root task gets promoted to ready."""
    db = _TS()
    try:
        pid = _make_project(db)
        t1 = _make_task(db, pid, "Task A", status="running")

        # The factory service uses get_db_session() internally.
        svc.restart_project(pid, mode="partial")

        # Root task: reset to pending, then promoted to ready
        assert _task_status(db, t1) == "ready", (
            f"Expected 'ready' (resets to pending then promoted) "
            f"but got '{_task_status(db, t1)}'"
        )
    finally:
        db.close()


def test_restart_resets_running_non_root(svc):
    """A non-root running task with incomplete deps stays pending after reset."""
    db = _TS()
    try:
        pid = _make_project(db)
        t_parent = _make_task(db, pid, "Parent", status="running")
        t_child = _make_task(db, pid, "Child", status="running")
        _make_edge(db, pid, t_parent, t_child)

        svc.restart_project(pid, mode="partial")

        # Both got reset to pending. Parent is root -> promoted to ready.
        # Child depends on Parent which is now ready (not completed) -> stays pending.
        assert _task_status(db, t_parent) == "ready", (
            "Root running task should become ready after restart"
        )
        assert _task_status(db, t_child) == "pending", (
            "Child of a running-dep (not completed) should stay pending"
        )
    finally:
        db.close()


# ── Bug 2: Partial restart promotes non-root pending tasks ─────────────


def test_restart_promotes_non_root_pending(svc):
    """Non-root task reset to pending should be promoted to ready when its
    dependency is already completed."""
    db = _TS()
    try:
        pid = _make_project(db)
        t_root = _make_task(db, pid, "Root Task", status="completed")
        t_child = _make_task(db, pid, "Child Task", status="failed")
        _make_edge(db, pid, t_root, t_child)

        svc.restart_project(pid, mode="partial")

        # Child should now be promoted to ready (Root is completed)
        assert _task_status(db, t_child) == "ready", (
            f"Expected 'ready' but got '{_task_status(db, t_child)}'"
        )
    finally:
        db.close()


# ── Bug 3: Full restart resets all tasks ───────────────────────────────


def test_restart_full_resets_all(svc):
    """Full restart resets ALL tasks regardless of current status."""
    db = _TS()
    try:
        pid = _make_project(db)
        t1 = _make_task(db, pid, "Task 1", status="completed", result={"ok": True})
        t2 = _make_task(db, pid, "Task 2", status="running")
        t3 = _make_task(db, pid, "Task 3", status="failed", error="oops")
        t4 = _make_task(db, pid, "Task 4", status="pending")

        svc.restart_project(pid, mode="full")

        # All tasks should be reset to pending (or ready if root)
        for tid, title in [(t1, "T1"), (t2, "T2"), (t3, "T3"), (t4, "T4")]:
            n = db.query(FactoryNode).filter(FactoryNode.id == tid).first()
            assert n is not None, f"{title} not found"
            assert n.result is None, f"{title} result not cleared"
            assert n.error is None, f"{title} error not cleared"
            assert n.retries == 0, f"{title} retries not reset"
    finally:
        db.close()


# ── _promote_eligible_tasks skips blocked ──────────────────────────────


def test_promote_eligible_tasks_skips_blocked(svc):
    """A pending task whose dependency is NOT completed must stay pending."""
    db = _TS()
    try:
        pid = _make_project(db)
        t_root = _make_task(db, pid, "Root Task", status="pending")  # NOT completed
        t_child = _make_task(db, pid, "Child Task", status="pending")
        _make_edge(db, pid, t_root, t_child)

        # Call the helper directly
        promoted = svc._promote_eligible_tasks(db, pid)

        # Root should be promoted (no deps), child should NOT (root not completed)
        assert _task_status(db, t_root) == "ready"
        assert _task_status(db, t_child) == "pending", (
            "Child should stay pending when dependency is not completed"
        )
        assert promoted == 1, "Expected exactly 1 promotion (root only)"
    finally:
        db.close()


# ── Edge: partial restart with mixed statuses ──────────────────────────
# Make sure running + failed tasks both get reset, and completed stays.


def test_restart_partial_preserves_completed(svc):
    """Partial restart must NOT reset completed tasks."""
    db = _TS()
    try:
        pid = _make_project(db)
        t_done = _make_task(db, pid, "Done", status="completed")
        t_fail = _make_task(db, pid, "Failed", status="failed")

        svc.restart_project(pid, mode="partial")

        assert _task_status(db, t_done) == "completed", "Completed task must stay completed"
        assert _task_status(db, t_fail) == "ready", (
            "Failed task should become ready (resets to pending, then promoted)"
        )
    finally:
        db.close()


# ── Edge: non-existent project returns None ────────────────────────────


def test_restart_nonexistent_project(svc):
    """restart_project should return None for a project that does not exist."""
    result = svc.restart_project(99999, mode="full")
    assert result is None
