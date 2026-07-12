"""
Tests for auto-split functionality in the Factory orchestrator.

Verifies that:
  1. _estimate_task_tokens estimates correctly for frontend & backend tasks.
  2. reroute_dependencies re-wires edges correctly.
  3. _try_split_task returns False for small tasks (no LLM call).
  4. _try_split_task creates sub-tasks, re-routes deps, and marks original done.
"""

import sys
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import services.factory_models  # noqa: F401 — registers tables with Base.metadata
from services.factory_service import FactoryService
from services.factory_models import FactoryProject, FactoryNode, FactoryEdge

# In-memory engine so we don't mutate global SessionLocal.
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
    """Point core.database.SessionLocal at our test engine."""
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    monkeypatch.setattr(cdb, "engine", _ENGINE)


@pytest.fixture
def svc():
    return FactoryService()


# ── Helpers ────────────────────────────────────────────────────────────


def _make_project(db) -> int:
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


def _get_dependents(db, task_id: int) -> list:
    """Return list of to_node_ids that depend on task_id."""
    edges = db.query(FactoryEdge).filter(
        FactoryEdge.from_node_id == task_id
    ).all()
    return [e.to_node_id for e in edges]


# ── Test: estimate tokens ─────────────────────────────────────────────


def test_estimate_task_tokens_frontend():
    """A frontend task with 14 features should estimate > 8000 tokens.

    Each feature is >8 chars after comma-splitting, and the profile
    (base=600, per_feat=550) yields 600 + 14*550 = 8300 tokens.
    """
    from services.factory_orchestrator import _estimate_task_tokens

    task = {
        "title": "Comprehensive frontend rebuild",
        "description": (
            "Implement responsive navigation, mobile hamburger menu toggle, "
            "smooth scroll behavior for anchor link navigation, "
            "interactive image lightbox gallery with touch swipe support, "
            "client-side form validation with real-time inline feedback, "
            "scroll-triggered reveal animations with Intersection Observer API, "
            "lazy-loaded images with blur-up placeholder previews, "
            "dark mode toggle with persisted localStorage preference, "
            "cookie consent banner with GDPR-compliant opt-in flow, "
            "back-to-top button with smooth auto-scroll behavior, "
            "live search with debounced input and results dropdown, "
            "responsive data table with column sorting and row filtering, "
            "keyboard navigation support for full WCAG 2.1 compliance, "
            "page transition loading spinner with progress indicator"
        ),
        "task_type": "frontend",
        "filename": "js/main.js",
    }
    est = _estimate_task_tokens(task)
    assert est > 8000, (
        f"Expected >8000 for 14-feature frontend task, got {est}"
    )


def test_estimate_task_tokens_small():
    """A simple backend task with 1 feature should estimate < 4000 tokens."""
    from services.factory_orchestrator import _estimate_task_tokens

    task = {
        "title": "Config file",
        "description": "Create docker-compose.yml",
        "task_type": "devops",
        "filename": "docker-compose.yml",
    }
    est = _estimate_task_tokens(task)
    assert est < 4000, (
        f"Expected <4000 for simple devops task, got {est}"
    )


# ── Test: reroute_dependencies ────────────────────────────────────────


def test_reroute_dependencies(svc):
    """Re-route B's dependency from A to [A1, A2].

    Graph before: A -> B
    After:      A1 -> B, A2 -> B   (A->B edge gone)
    """
    db = _TS()
    try:
        pid = _make_project(db)
        a_id = _make_task(db, pid, "A", status="completed")
        b_id = _make_task(db, pid, "B", status="pending")
        _make_edge(db, pid, a_id, b_id)

        a1_id = _make_task(db, pid, "A1", status="pending")
        a2_id = _make_task(db, pid, "A2", status="pending")

        # Re-route: B now depends on A1 and A2 instead of A
        count = svc.reroute_dependencies(pid, a_id, [a1_id, a2_id])

        assert count == 1, f"Expected 1 edge re-routed, got {count}"

        # Old edge A->B should be gone
        deps_of_b = _get_dependents(db, a_id)
        assert b_id not in deps_of_b, (
            "Old A->B edge should have been removed"
        )

        # New edges A1->B and A2->B should exist
        deps_of_a1 = _get_dependents(db, a1_id)
        deps_of_a2 = _get_dependents(db, a2_id)
        assert b_id in deps_of_a1, "A1->B edge missing"
        assert b_id in deps_of_a2, "A2->B edge missing"
    finally:
        db.close()


def test_reroute_dependencies_no_self_loops(svc):
    """reroute_dependencies must not create self-loops."""
    db = _TS()
    try:
        pid = _make_project(db)
        a_id = _make_task(db, pid, "A", status="completed")
        b_id = _make_task(db, pid, "B", status="pending")
        _make_edge(db, pid, a_id, b_id)

        # Re-route with a new_node_id that happens to be B itself
        count = svc.reroute_dependencies(pid, a_id, [a_id, b_id])

        # Should still work
        assert count == 1

        # No self-loop on B
        deps_of_b = _get_dependents(db, b_id)
        assert b_id not in deps_of_b, "Self-loop should not be created"
    finally:
        db.close()


# ── Test: _try_split_task returns False for small tasks ───────────────


@pytest.mark.asyncio
async def test_try_split_returns_false_for_small_task(svc, monkeypatch):
    """When estimate <= threshold, _try_split_task returns False without
    calling the LLM."""
    from services.factory_orchestrator import _try_split_task

    # Mock estimate to return a small number
    monkeypatch.setattr(
        "services.factory_orchestrator._estimate_task_tokens",
        lambda task: 500,
    )

    # Mock _get_produce_max_tokens to return a large budget
    monkeypatch.setattr(
        "services.factory_orchestrator._get_produce_max_tokens",
        lambda: 16384,
    )

    # Ensure _call_agent is never called
    call_agent_mock = AsyncMock()
    monkeypatch.setattr(
        "services.factory_orchestrator._call_agent",
        call_agent_mock,
    )

    db = _TS()
    try:
        pid = _make_project(db)
        task_id = _make_task(
            db, pid, "Small Task", status="ready",
            task_type="backend", filename="small.py",
        )
        _service = svc
        monkeypatch.setattr(
            "services.factory_orchestrator._service",
            _service,
        )

        task = {
            "id": task_id,
            "title": "Small Task",
            "description": "A tiny task",
            "task_type": "backend",
            "filename": "small.py",
            "dependencies": [],
        }

        result = await _try_split_task(pid, task, "default")
        assert result is False, (
            "Small task should NOT be split"
        )
        call_agent_mock.assert_not_called()
    finally:
        db.close()


# ── Test: _try_split_task creates sub-tasks ────────────────────────────


@pytest.mark.asyncio
async def test_try_split_creates_subtasks(svc, monkeypatch):
    """When estimate > threshold and LLM returns a split plan,
    _try_split_task creates sub-task nodes, re-routes deps, and
    marks original as completed."""
    from services.factory_orchestrator import _try_split_task

    # ── DB setup: project with root A (completed) and big task B (depends on A) ──
    db = _TS()
    try:
        pid = _make_project(db)
        a_id = _make_task(
            db, pid, "Root A", status="completed",
            task_type="backend", filename="root.py",
        )
        b_id = _make_task(
            db, pid, "Big Task B", status="ready",
            task_type="frontend", filename="big.js",
        )
        _make_edge(db, pid, a_id, b_id)

        # ── Mocks ──
        # Force the token estimate high
        monkeypatch.setattr(
            "services.factory_orchestrator._estimate_task_tokens",
            lambda task: 15000,
        )
        # Low budget so threshold is ~170, well below 15000
        monkeypatch.setattr(
            "services.factory_orchestrator._get_produce_max_tokens",
            lambda: 200,
        )
        # Mock the LLM call to return a split plan
        monkeypatch.setattr(
            "services.factory_orchestrator._call_agent",
            AsyncMock(return_value=(
                '{"split": true, "tasks": ['
                '{"title": "Sub one", "description": "Part 1", "filename": "part1.js"},'
                '{"title": "Sub two", "description": "Part 2", "filename": "part2.js"}'
                "]}"
            )),
        )
        monkeypatch.setattr(
            "services.factory_orchestrator._service",
            svc,
        )

        task = {
            "id": b_id,
            "title": "Big Task B",
            "description": (
                "Implement hero, navigation, gallery, "
                "contact form, footer, and animations"
            ),
            "task_type": "frontend",
            "filename": "big.js",
            "dependencies": [a_id],
            "assigned_agent": "",
        }

        # ── Act ──
        result = await _try_split_task(pid, task, "default")

        # ── Assert ──
        assert result is True, "Big task should be split"

        # Original task should be completed
        assert _task_status(db, b_id) == "completed", (
            "Original task should be marked completed"
        )

        # Two new sub-task nodes should exist
        all_nodes = svc.get_nodes(pid)
        sub_nodes = [n for n in all_nodes if n["id"] != a_id and n["id"] != b_id]
        assert len(sub_nodes) == 2, (
            f"Expected 2 sub-task nodes, got {len(sub_nodes)}: "
            f"{[n['title'] for n in sub_nodes]}"
        )

        # Sub-tasks should be ready or pending (promoted by orchestrator)
        for sn in sub_nodes:
            assert sn["status"] in ("ready", "pending"), (
                f"Sub-task '{sn['title']}' should be ready/pending, "
                f"got '{sn['status']}'"
            )

        # Sub-tasks should have inherited the dependency on A
        for sn in sub_nodes:
            # Check edges: there should be an edge from A to this sub-task
            sub_id = sn["id"]
            a_edges = db.query(FactoryEdge).filter(
                FactoryEdge.from_node_id == a_id,
                FactoryEdge.to_node_id == sub_id,
            ).all()
            assert len(a_edges) == 1, (
                f"Sub-task '{sn['title']}' should inherit dep on A"
            )

        # No task should depend directly on B anymore
        b_dependents = _get_dependents(db, b_id)
        assert b_dependents == [], (
            f"B should have no dependents after split, got {b_dependents}"
        )

        # Original task's result should contain split metadata
        original = svc.get_node(b_id)
        assert original is not None
        result_val = original.get("result") or {}
        assert "split_into" in result_val, (
            "Completion result should contain split_into metadata"
        )
        assert len(result_val["split_into"]) == 2, (
            "split_into should list 2 sub-task ids"
        )
    finally:
        db.close()
