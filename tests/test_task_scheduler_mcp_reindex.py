"""Regression tests for Finding 5 — background LLM tasks must reindex MCP
tools before the RAG lane fires, otherwise the first run after a fresh
container (or any run before a foreground chat that lazy-indexes) misses
every ``mcp__oxs_*`` tool the OXS extensions registered.

The foreground chat path triggers ``tool_idx.index_mcp_tools(mcp_mgr)``
implicitly (see ``src/agent_loop.py`` near the tool-rag block).
``TaskScheduler._execute_llm_task`` is the only path that forgets — without
this fix, an analyzer that depends on bridge MCP tools silently sees them
as ``unoffered textual call`` and the model emits ``I cannot access ...``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, DateTime, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _setup_db(tmp_path, monkeypatch):
    """Minimal ScheduledTask + CrewMember tables for ``_execute_llm_task``."""
    base = declarative_base()

    class CrewMember(base):
        __tablename__ = "crew_members"
        id = Column(String, primary_key=True)
        name = Column(String)
        enabled_tools = Column(Text)
        personality = Column(Text)
        endpoint_url = Column(String)
        model = Column(String)
        is_default_assistant = Column(String)

    class ScheduledTask(base):
        __tablename__ = "scheduled_tasks"
        id = Column(String, primary_key=True)
        owner = Column(String)
        name = Column(String)
        task_type = Column(String, default="llm")
        action = Column(String)
        status = Column(String, default="active")
        next_run = Column(DateTime)
        last_run = Column(DateTime)
        prompt = Column(Text, default="")
        request_authority_json = Column(Text)
        trigger_type = Column(String, default="schedule")
        schedule = Column(String, default="daily")
        scheduled_time = Column(String, default="08:00")
        scheduled_day = Column(String)
        scheduled_date = Column(DateTime)
        cron_expression = Column(String)
        endpoint_url = Column(String)
        endpoint_id = Column(String)
        model = Column(String)
        session_id = Column(String)
        crew_member_id = Column(String)
        character_id = Column(String)
        max_steps = Column(String, default="5")

    class TaskRun(base):
        __tablename__ = "task_runs"
        id = Column(String, primary_key=True)
        task_id = Column(String)
        started_at = Column(DateTime)
        finished_at = Column(DateTime)
        status = Column(String)
        result = Column(Text)
        error = Column(Text)
        model = Column(String)

    class Session(base):
        __tablename__ = "sessions"
        id = Column(String, primary_key=True)
        name = Column(String)
        endpoint_url = Column(String)
        model = Column(String)
        owner = Column(String)
        folder = Column(String)
        created_at = Column(DateTime)
        updated_at = Column(DateTime)

    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return session_local, ScheduledTask, Session


def _make_task(prompt="run the digest", session_id="s"):
    return SimpleNamespace(
        id="t-mcp-reindex",
        crew_member_id=None,
        endpoint_url="http://ep/v1",
        model="m",
        session_id=session_id,
        owner="admin",
        prompt=prompt,
        name="mcp-reindex-job",
        max_steps=5,
        character_id=None,
    )


@pytest.fixture
def patched_dependencies(monkeypatch):
    """Stub everything ``_execute_llm_task`` needs besides the RAG lane,
    so the test can focus on whether ``index_mcp_tools`` is invoked in the
    right place relative to ``get_tools_for_query``."""
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: [] if key == "disabled_tools" else default,
    )
    monkeypatch.setattr("src.task_endpoint.resolve_task_candidates", lambda **kw: [])

    # Don't actually run the agent loop; just record the kwargs.
    captured = {}

    async def _stub_run_agent_loop(self, endpoint_url, model, task, session_id,
                                  **kwargs):
        captured["relevant_tools"] = kwargs.get("relevant_tools")
        return "stub-result"

    monkeypatch.setattr("src.task_scheduler.TaskScheduler._run_agent_loop",
                        _stub_run_agent_loop)


@pytest.mark.asyncio
async def test_execute_llm_task_reindexes_mcp_tools_before_rag(tmp_path, monkeypatch,
                                                              patched_dependencies):
    """The whole point of Finding 5: an MCP tool registered BEFORE the task
    fires must be picked up by the RAG lane, even if no foreground chat
    has run since container start."""
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)
    with session_local() as db:
        db.add(Session(id="s", name="existing", endpoint_url="http://ep/v1",
                       model="m", owner="admin", folder="Tasks",
                       created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                       updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()

    # Two fake collaborators: the tool index (records the order of calls)
    # and the MCP manager (returns a generation that simulates a fresh
    # reindex, forcing ``index_mcp_tools`` to actually do work).
    calls = []

    class FakeToolIndex:
        def __init__(self):
            self._indexed_mcp = False

        def index_mcp_tools(self, mcp_mgr, disabled_map=None):
            calls.append("index_mcp_tools")
            self._indexed_mcp = True
            mcp_mgr._generation = getattr(mcp_mgr, "_generation", 0)

        def get_tools_for_query(self, query, k=8, always_include=None):
            calls.append("get_tools_for_query")
            base = {"manage_calendar", "manage_notes"}
            if always_include:
                base.update(always_include)
            if self._indexed_mcp:
                base.add("mcp__oxs_w2a__list_messages")
            return base

    class FakeMcpMgr:
        _generation = 1  # pretend something was registered after startup

    fake_idx = FakeToolIndex()
    fake_mgr = FakeMcpMgr()
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: fake_idx)
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: fake_mgr)

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)
    task = _make_task(prompt="recent WhatsApp conversations")
    await sched._execute_llm_task(task, db=None)

    # The reindex MUST happen before the RAG query. Otherwise the first call
    # returns the stale pre-reindex set and the LLM never sees the MCP tool.
    assert calls == ["index_mcp_tools", "get_tools_for_query"], (
        f"expected index_mcp_tools before get_tools_for_query; got: {calls}"
    )


@pytest.mark.asyncio
async def test_execute_llm_task_survives_when_mcp_indexing_times_out(
        tmp_path, monkeypatch, patched_dependencies):
    """The reindex call hits ChromaDB and can stall on a cold remote
    endpoint. A timeout must be tolerated — falling back to the previous
    RAG set is strictly better than crashing the task."""
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)
    with session_local() as db:
        db.add(Session(id="s", name="existing", endpoint_url="http://ep/v1",
                       model="m", owner="admin", folder="Tasks",
                       created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                       updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()

    def slow_index(*args, **kwargs):
        raise asyncio.TimeoutError("chroma cold-start")

    fake_idx = MagicMock()
    fake_idx.index_mcp_tools.side_effect = slow_index
    fake_idx.get_tools_for_query.return_value = {"manage_calendar"}
    fake_mgr = MagicMock()
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: fake_idx)
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: fake_mgr)

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)
    task = _make_task(prompt="any prompt")
    # Must not raise — the surrounding try/except should swallow the timeout.
    await sched._execute_llm_task(task, db=None)

    fake_idx.index_mcp_tools.assert_called_once()
    fake_idx.get_tools_for_query.assert_called_once_with("any prompt", k=8, always_include=None)


@pytest.mark.asyncio
async def test_execute_llm_task_skips_reindex_when_mcp_mgr_missing(
        tmp_path, monkeypatch, patched_dependencies):
    """The path still works when no MCP server is registered — calls to
    ``index_mcp_tools`` are simply skipped."""
    session_local, ScheduledTask, Session = _setup_db(tmp_path, monkeypatch)
    with session_local() as db:
        db.add(Session(id="s", name="existing", endpoint_url="http://ep/v1",
                       model="m", owner="admin", folder="Tasks",
                       created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                       updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        db.commit()

    fake_idx = MagicMock()
    fake_idx.get_tools_for_query.return_value = {"manage_calendar"}
    monkeypatch.setattr("src.tool_index.get_tool_index", lambda: fake_idx)
    monkeypatch.setattr("src.mcp_manager.get_mcp_manager", lambda: None)

    from src.task_scheduler import TaskScheduler
    sched = TaskScheduler(session_manager=None)
    task = _make_task(prompt="any prompt")
    await sched._execute_llm_task(task, db=None)

    # When get_mcp_manager returns None, index_mcp_tools must NOT be called.
    fake_idx.index_mcp_tools.assert_not_called()
    fake_idx.get_tools_for_query.assert_called_once_with("any prompt", k=8, always_include=None)
