import asyncio
import sys
import types
from datetime import datetime

import pytest

from src import agent_goals
from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_schemas import function_call_to_tool_block


class _Column:
    def __init__(self, name):
        self.name = name

    def __eq__(self, value):
        return lambda row: getattr(row, self.name, None) == value


class _SessionRow:
    id = _Column("id")
    owner = _Column("owner")

    def __init__(self, id, owner):
        self.id = id
        self.owner = owner


class _GoalRow:
    session_id = _Column("session_id")
    owner = _Column("owner")

    def __init__(self, **kwargs):
        self.session_id = kwargs.get("session_id")
        self.goal_id = kwargs.get("goal_id")
        self.owner = kwargs.get("owner")
        self.objective = kwargs.get("objective")
        self.status = kwargs.get("status", "active")
        self.token_budget = kwargs.get("token_budget")
        self.tokens_used = kwargs.get("tokens_used", 0)
        self.time_used_seconds = kwargs.get("time_used_seconds", 0)
        self.continuation_count = kwargs.get("continuation_count", 0)
        self.created_at = kwargs.get("created_at") or datetime.utcnow()
        self.updated_at = kwargs.get("updated_at") or datetime.utcnow()


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.predicates = []

    def filter(self, *predicates):
        self.predicates.extend(predicates)
        return self

    def first(self):
        for row in self.rows:
            if all(pred(row) for pred in self.predicates):
                return row
        return None


class _FakeDb:
    def __init__(self, store):
        self.store = store

    def query(self, model):
        if model is _SessionRow:
            return _Query(list(self.store["sessions"].values()))
        if model is _GoalRow:
            return _Query(list(self.store["goals"].values()))
        return _Query([])

    def add(self, row):
        if isinstance(row, _SessionRow):
            self.store["sessions"][row.id] = row
        elif isinstance(row, _GoalRow):
            self.store["goals"][row.session_id] = row

    def delete(self, row):
        if isinstance(row, _GoalRow):
            self.store["goals"].pop(row.session_id, None)

    def commit(self):
        pass

    def rollback(self):
        pass

    def refresh(self, row):
        pass

    def close(self):
        pass


@pytest.fixture()
def fake_goal_db(monkeypatch):
    store = {
        "sessions": {
            "s1": _SessionRow("s1", "alice"),
            "s2": _SessionRow("s2", "bob"),
        },
        "goals": {},
    }
    module = types.ModuleType("core.database")
    module.Session = _SessionRow
    module.AgentGoal = _GoalRow
    module.SessionLocal = lambda: _FakeDb(store)
    monkeypatch.setitem(sys.modules, "core.database", module)
    return store


def test_create_get_and_duplicate_model_goal(fake_goal_db):
    goal = agent_goals.create_goal("s1", "Ship goal system", token_budget=1000, owner="alice")
    assert goal["status"] == "active"
    assert goal["remaining_tokens"] == 1000

    fetched = agent_goals.get_goal("s1", owner="alice")
    assert fetched["objective"] == "Ship goal system"

    with pytest.raises(agent_goals.GoalConflictError):
        agent_goals.create_goal("s1", "Second goal", owner="alice")


def test_ui_replace_resets_usage(fake_goal_db):
    first = agent_goals.set_goal("s1", "First", token_budget=10, owner="alice", replace=True)
    agent_goals.account_goal_usage("s1", {"input_tokens": 6, "output_tokens": 5}, owner="alice", goal_id=first["goal_id"])
    replaced = agent_goals.set_goal("s1", "Second", token_budget=None, owner="alice", replace=True)
    assert replaced["objective"] == "Second"
    assert replaced["tokens_used"] == 0
    assert replaced["token_budget"] is None
    assert replaced["goal_id"] != first["goal_id"]


def test_model_update_only_complete_or_blocked(fake_goal_db):
    agent_goals.create_goal("s1", "Do it", owner="alice")
    complete = agent_goals.update_goal_from_model("s1", "complete", owner="alice")
    assert complete["status"] == "complete"

    with pytest.raises(agent_goals.GoalError):
        agent_goals.update_goal_from_model("s1", "paused", owner="alice")


def test_accounting_crosses_budget(fake_goal_db):
    goal = agent_goals.create_goal("s1", "Budgeted", token_budget=12, owner="alice")
    updated = agent_goals.account_goal_usage(
        "s1",
        {"input_tokens": 7, "output_tokens": 5},
        elapsed_seconds=2.4,
        owner="alice",
        goal_id=goal["goal_id"],
    )
    assert updated["tokens_used"] == 12
    assert updated["time_used_seconds"] == 2
    assert updated["status"] == "budget_limited"
    ok, reason, _ = agent_goals.can_continue_goal("s1", owner="alice")
    assert ok is False
    assert reason == "budget_limited"


def test_owner_scope(fake_goal_db):
    agent_goals.create_goal("s1", "Private", owner="alice")
    assert agent_goals.get_goal("s1", owner="bob") is None
    with pytest.raises(agent_goals.GoalNotFoundError):
        agent_goals.create_goal("s1", "Wrong owner", owner="bob")


def test_goal_routes_are_session_scoped():
    source = open("routes/goal_routes.py", encoding="utf-8").read()
    assert 'APIRouter(prefix="/api/goals"' in source
    assert "_verify_session_owner(request, session_id)" in source
    assert "effective_user(request)" in source
    assert '@router.post("/{session_id}/continue")' in source
    assert "start_goal_continuation(session_id" in source


def test_incognito_chat_disables_goal_tools():
    source = open("routes/chat_routes.py", encoding="utf-8").read()
    assert '"get_goal", "create_goal", "update_goal"' in source
    assert "goals_enabled=not incognito" in source
    assert '"goal_update", "goal_cleared"' in source


def test_goal_runner_preserves_agent_loop_tool_metadata():
    source = open("src/goal_runner.py", encoding="utf-8").read()
    assert 'last_metrics.get("tool_events") or tool_events' in source


def test_goal_runner_starts_detached_run(monkeypatch):
    from src import goal_runner

    class _Session:
        owner = "alice"
        endpoint_url = "https://example.test/v1/chat/completions"
        model = "model"

    class _SessionManager:
        def get_session(self, session_id):
            assert session_id == "s1"
            return _Session()

    started = {}
    monkeypatch.setattr(goal_runner.agent_runs, "is_active", lambda session_id: False)
    monkeypatch.setattr(goal_runner, "_session_manager", lambda: _SessionManager())
    monkeypatch.setattr(goal_runner, "can_continue_goal", lambda session_id, owner=None: (
        True,
        "active",
        {"session_id": session_id, "status": "active"},
    ))
    monkeypatch.setattr(goal_runner, "mark_continuation_started", lambda session_id, owner=None: {
        "session_id": session_id,
        "status": "active",
        "continuation_count": 1,
    })
    monkeypatch.setattr(goal_runner.agent_runs, "start", lambda session_id, agen: started.update({
        "session_id": session_id,
        "agen": agen,
    }))

    ok, reason, goal = goal_runner.start_goal_continuation("s1", owner="alice")

    assert ok is True
    assert reason == "started"
    assert goal["continuation_count"] == 1
    assert started["session_id"] == "s1"


def test_goal_runner_refuses_when_run_active(monkeypatch):
    from src import goal_runner

    monkeypatch.setattr(goal_runner.agent_runs, "is_active", lambda session_id: True)
    monkeypatch.setattr(goal_runner, "can_continue_goal", lambda session_id, owner=None: (
        True,
        "active",
        {"session_id": session_id, "status": "active"},
    ))

    ok, reason, goal = goal_runner.start_goal_continuation("s1", owner="alice")

    assert ok is False
    assert reason == "run_active"
    assert goal["status"] == "active"


def test_goal_function_call_conversion():
    block = function_call_to_tool_block("create_goal", '{"objective":"Ship it","token_budget":50}')
    assert block is not None
    assert block.tool_type == "create_goal"
    assert '"objective": "Ship it"' in block.content


def test_goal_tool_dispatch(fake_goal_db):
    desc, result = asyncio.run(execute_tool_block(
        ToolBlock("create_goal", '{"objective":"Tool goal","token_budget":20}'),
        session_id="s1",
        owner="alice",
    ))
    assert desc == "create_goal"
    assert result["exit_code"] == 0
    assert result["goal"]["objective"] == "Tool goal"

    _, bad = asyncio.run(execute_tool_block(
        ToolBlock("update_goal", '{"status":"paused"}'),
        session_id="s1",
        owner="alice",
    ))
    assert bad["exit_code"] == 1
    assert "status must be one of" in bad["error"]
