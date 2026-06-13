# tests/test_subagent_sessions.py
"""Multiagent slice-1 Task 3: persisted child sessions for subagents."""
import pytest

from core.database import SessionLocal, Session as DbSession
from core.session_manager import SessionManager


@pytest.fixture()
def mgr():
    return SessionManager()


def test_child_session_created_and_linked(mgr):
    parent = mgr.create_session("ma-parent-1", "Coordinator",
                                "http://e", "m", owner="oleg")
    child = mgr.create_subagent_session(
        "ma-child-1", "researcher run", "http://e", "m",
        agent_owner="agent:oleg/researcher", human_owner="oleg",
        parent_session_id=parent.id)
    assert child.owner == "agent:oleg/researcher"
    assert child.meta == {"parent_session_id": "ma-parent-1",
                          "human_owner": "oleg", "kind": "subagent"}
    db = SessionLocal()
    try:
        row = db.query(DbSession).filter(DbSession.id == "ma-child-1").one()
        assert row.owner == "agent:oleg/researcher"
        assert row.meta["kind"] == "subagent"
        assert row.meta["parent_session_id"] == "ma-parent-1"
        assert row.meta["human_owner"] == "oleg"
    finally:
        db.close()


def test_child_session_requires_agent_owner(mgr):
    with pytest.raises(ValueError, match="agent"):
        mgr.create_subagent_session(
            "ma-child-2", "x", "http://e", "m",
            agent_owner="oleg",            # human id — refused
            human_owner="oleg", parent_session_id="ma-parent-1")


def test_owner_gating_hides_child_from_other_users(mgr):
    mgr.create_session("ma-parent-3", "Coordinator", "http://e", "m",
                       owner="oleg")
    mgr.create_subagent_session(
        "ma-child-3", "run", "http://e", "m",
        agent_owner="agent:oleg/researcher", human_owner="oleg",
        parent_session_id="ma-parent-3")
    # The DB owner is the agent id: existing per-owner queries scoped to a
    # human never return the child (the human reaches it via the parent link).
    ids = set(mgr.get_sessions_for_user("oleg"))   # dict keyed by session id
    assert "ma-parent-3" in ids and "ma-child-3" not in ids
    agent_ids = set(mgr.get_sessions_for_user("agent:oleg/researcher"))
    assert "ma-child-3" in agent_ids
