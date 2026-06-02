"""Tests for routes/swarm_routes.py and routes/diagnostics_routes.py.

Covers CRUD operations for tasks, agents, memory, logs, and events in the
Tech Duinn swarm orchestrator, plus the diagnostics health/stats endpoints.
"""

import asyncio
import sqlite3
import sys
import types
import pytest
from unittest.mock import MagicMock, AsyncMock

from fastapi import HTTPException

# ── Stub modules required by diagnostics_routes at import time ─────────
# diagnostics_routes does ``from services.youtube.youtube_handler import ...``
# at the top level, so the stub must exist before we import that module.

_youtube_mod = types.ModuleType("services.youtube.youtube_handler")
_youtube_mod.extract_youtube_id = MagicMock(return_value="test123")
_youtube_mod.extract_transcript_async = AsyncMock(
    return_value={"success": True, "transcript": "hello world"},
)

for _parent in ("services", "services.youtube"):
    if _parent not in sys.modules:
        _p = types.ModuleType(_parent)
        _p.__path__ = []
        sys.modules[_parent] = _p

sys.modules["services.youtube.youtube_handler"] = _youtube_mod

# ── Imports (after stubs are in place) ─────────────────────────────────

from routes.swarm_routes import (
    setup_swarm_routes,
    _init_tables,
    TaskCreate,
    TaskUpdate,
    AgentRegister,
    AgentHeartbeat,
    LogWrite,
    EventPublish,
    MemorySet,
)
from routes.diagnostics_routes import setup_diagnostics_routes
import routes.diagnostics_routes as diag_mod


# ── Helpers ────────────────────────────────────────────────────────────

def _find_endpoint(router, path, method="GET"):
    """Return the endpoint callable for *method* *path* on *router*."""
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", {""}):
            return route.endpoint
    raise AssertionError(f"Route {method} {path} not found on router")


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def swarm_router(monkeypatch):
    """Provide a swarm router backed by an in-memory SQLite database."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    monkeypatch.setattr("routes.swarm_routes._get_conn", lambda: conn)
    return setup_swarm_routes()


@pytest.fixture
def diag_router(monkeypatch):
    """Provide a diagnostics router with mocked dependencies."""
    # Stub core.database.get_detailed_stats (used via a lazy import inside
    # the /api/db/stats handler).
    core_db = types.ModuleType("core.database")
    core_db.get_detailed_stats = MagicMock(
        return_value={"tables": 5, "total_rows": 100},
    )
    monkeypatch.setitem(sys.modules, "core.database", core_db)

    # Patch the youtube helpers that diagnostics_routes already imported.
    monkeypatch.setattr(
        diag_mod, "extract_youtube_id", MagicMock(return_value="abc123"),
    )
    monkeypatch.setattr(
        diag_mod, "extract_transcript_async",
        AsyncMock(return_value={"success": True, "transcript": "hello world"}),
    )

    rag_manager = MagicMock()
    rag_manager.get_stats.return_value = {"chunks": 50, "documents": 10}

    research_handler = MagicMock()
    research_handler.call_research_service = AsyncMock(return_value="research result text")

    return setup_diagnostics_routes(rag_manager, rag_available=True, research_handler=research_handler)


# ======================================================================
# Tasks
# ======================================================================

class TestSwarmTasks:

    def test_create_task(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        result = asyncio.run(ep(body=TaskCreate(title="deploy prod", priority=8)))
        assert result["title"] == "deploy prod"
        assert result["status"] == "pending"
        assert result["priority"] == 8
        assert "id" in result

    def test_list_tasks_empty(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/tasks", "GET")
        result = asyncio.run(ep())
        assert result == []

    def test_list_tasks_after_create(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        asyncio.run(create(body=TaskCreate(title="t1")))
        asyncio.run(create(body=TaskCreate(title="t2")))
        asyncio.run(create(body=TaskCreate(title="t3")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/tasks", "GET")
        result = asyncio.run(list_ep())
        assert len(result) == 3

    def test_list_tasks_filter_by_status(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        t1 = asyncio.run(create(body=TaskCreate(title="t1")))
        asyncio.run(create(body=TaskCreate(title="t2")))

        # Mark t1 as completed
        update = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "PATCH")
        asyncio.run(update(task_id=t1["id"], body=TaskUpdate(status="completed")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/tasks", "GET")
        pending = asyncio.run(list_ep(status="pending"))
        completed = asyncio.run(list_ep(status="completed"))
        assert len(pending) == 1
        assert len(completed) == 1

    def test_get_task(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        created = asyncio.run(create(body=TaskCreate(title="my task")))

        get_ep = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "GET")
        result = asyncio.run(get_ep(task_id=created["id"]))
        assert result["title"] == "my task"
        assert result["id"] == created["id"]

    def test_get_task_not_found(self, swarm_router):
        get_ep = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "GET")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_ep(task_id="does-not-exist"))
        assert exc.value.status_code == 404

    def test_update_task_status_to_completed(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        created = asyncio.run(create(body=TaskCreate(title="build")))

        update = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "PATCH")
        result = asyncio.run(update(task_id=created["id"], body=TaskUpdate(status="completed", result="success")))
        assert result["status"] == "completed"
        assert result["result"] == "success"
        assert result["completed_at"] is not None

    def test_update_task_no_changes_returns_original(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        created = asyncio.run(create(body=TaskCreate(title="unchanged")))

        update = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "PATCH")
        result = asyncio.run(update(task_id=created["id"], body=TaskUpdate()))
        assert result["title"] == "unchanged"
        assert result["status"] == "pending"

    def test_update_task_not_found(self, swarm_router):
        update = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "PATCH")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(update(task_id="nope", body=TaskUpdate(status="completed")))
        assert exc.value.status_code == 404

    def test_delete_task(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        created = asyncio.run(create(body=TaskCreate(title="doomed")))

        delete = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "DELETE")
        result = asyncio.run(delete(task_id=created["id"]))
        assert result["status"] == "deleted"

        # Confirm it is gone
        get_ep = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "GET")
        with pytest.raises(HTTPException):
            asyncio.run(get_ep(task_id=created["id"]))

    def test_delete_task_not_found(self, swarm_router):
        delete = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "DELETE")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(delete(task_id="ghost"))
        assert exc.value.status_code == 404

    def test_search_tasks(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        asyncio.run(create(body=TaskCreate(title="deploy nginx server", tags="ops")))
        asyncio.run(create(body=TaskCreate(title="write unit tests", tags="qa")))

        search = _find_endpoint(swarm_router, "/api/swarm/tasks/search/{query}", "GET")
        result = asyncio.run(search(query="deploy"))
        assert len(result) >= 1
        assert any("deploy" in r["title"] for r in result)

    def test_search_tasks_no_match(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        asyncio.run(create(body=TaskCreate(title="hello world")))

        search = _find_endpoint(swarm_router, "/api/swarm/tasks/search/{query}", "GET")
        result = asyncio.run(search(query="xyznonexistent"))
        assert result == []


# ======================================================================
# Agents
# ======================================================================

class TestSwarmAgents:

    def test_register_agent(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        result = asyncio.run(ep(body=AgentRegister(name="agent-alpha", role="researcher")))
        assert result["name"] == "agent-alpha"
        assert result["status"] == "online"
        assert "id" in result

    def test_list_agents_empty(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/agents", "GET")
        assert asyncio.run(ep()) == []

    def test_list_agents_multiple(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        asyncio.run(create(body=AgentRegister(name="a1")))
        asyncio.run(create(body=AgentRegister(name="a2")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/agents", "GET")
        assert len(asyncio.run(list_ep())) == 2

    def test_list_agents_filter_by_status(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        a1 = asyncio.run(create(body=AgentRegister(name="a1")))

        heartbeat = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}/heartbeat", "POST")
        asyncio.run(heartbeat(agent_id=a1["id"], body=AgentHeartbeat(status="busy")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/agents", "GET")
        online = asyncio.run(list_ep(status="online"))
        busy = asyncio.run(list_ep(status="busy"))
        assert len(online) == 0
        assert len(busy) == 1

    def test_get_agent(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        created = asyncio.run(create(body=AgentRegister(name="my-agent", role="coder")))

        get_ep = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}", "GET")
        result = asyncio.run(get_ep(agent_id=created["id"]))
        assert result["name"] == "my-agent"
        assert result["role"] == "coder"

    def test_get_agent_not_found(self, swarm_router):
        get_ep = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}", "GET")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_ep(agent_id="nope"))
        assert exc.value.status_code == 404

    def test_agent_heartbeat(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        created = asyncio.run(create(body=AgentRegister(name="hb-agent")))

        heartbeat = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}/heartbeat", "POST")
        result = asyncio.run(heartbeat(agent_id=created["id"], body=AgentHeartbeat(status="busy")))
        assert result["status"] == "ok"

        # Verify the status actually changed
        get_ep = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}", "GET")
        agent = asyncio.run(get_ep(agent_id=created["id"]))
        assert agent["status"] == "busy"

    def test_agent_heartbeat_not_found(self, swarm_router):
        heartbeat = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}/heartbeat", "POST")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(heartbeat(agent_id="ghost", body=AgentHeartbeat()))
        assert exc.value.status_code == 404

    def test_delete_agent(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        created = asyncio.run(create(body=AgentRegister(name="temp-agent")))

        delete = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}", "DELETE")
        result = asyncio.run(delete(agent_id=created["id"]))
        assert result["status"] == "deleted"

    def test_delete_agent_not_found(self, swarm_router):
        delete = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}", "DELETE")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(delete(agent_id="ghost"))
        assert exc.value.status_code == 404


# ======================================================================
# Memory
# ======================================================================

class TestSwarmMemory:

    def test_set_memory(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        result = asyncio.run(ep(body=MemorySet(key="db_url", value="postgres://localhost/app")))
        assert result["key"] == "db_url"
        assert result["namespace"] == "shared"
        assert "id" in result

    def test_set_memory_custom_namespace(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        result = asyncio.run(ep(body=MemorySet(key="k", value="v", namespace="alice")))
        assert result["namespace"] == "alice"

    def test_list_memory(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="k1", value="v1")))
        asyncio.run(create(body=MemorySet(key="k2", value="v2")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/memory", "GET")
        result = asyncio.run(list_ep())
        assert len(result) == 2

    def test_list_memory_by_namespace(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="shared_k", value="v", namespace="shared")))
        asyncio.run(create(body=MemorySet(key="private_k", value="v", namespace="alice")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/memory", "GET")
        shared = asyncio.run(list_ep(namespace="shared"))
        alice = asyncio.run(list_ep(namespace="alice"))
        assert len(shared) == 1
        assert len(alice) == 1

    def test_get_memory(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="mykey", value="myvalue")))

        get_ep = _find_endpoint(swarm_router, "/api/swarm/memory/{namespace}/{key}", "GET")
        result = asyncio.run(get_ep(namespace="shared", key="mykey"))
        assert result["value"] == "myvalue"
        assert result["key"] == "mykey"

    def test_get_memory_not_found(self, swarm_router):
        get_ep = _find_endpoint(swarm_router, "/api/swarm/memory/{namespace}/{key}", "GET")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_ep(namespace="shared", key="missing"))
        assert exc.value.status_code == 404

    def test_delete_memory(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="doomed", value="val")))

        delete = _find_endpoint(swarm_router, "/api/swarm/memory/{namespace}/{key}", "DELETE")
        result = asyncio.run(delete(namespace="shared", key="doomed"))
        assert result["status"] == "deleted"

    def test_delete_memory_not_found(self, swarm_router):
        delete = _find_endpoint(swarm_router, "/api/swarm/memory/{namespace}/{key}", "DELETE")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(delete(namespace="shared", key="ghost"))
        assert exc.value.status_code == 404

    def test_search_memory(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="database_url", value="postgres://prod/db")))
        asyncio.run(create(body=MemorySet(key="cache_ttl", value="300")))

        search = _find_endpoint(swarm_router, "/api/swarm/memory/search/{query}", "GET")
        result = asyncio.run(search(query="database"))
        assert len(result) >= 1
        assert any("database" in r["key"] for r in result)

    def test_search_memory_no_match(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")
        asyncio.run(create(body=MemorySet(key="hello", value="world")))

        search = _find_endpoint(swarm_router, "/api/swarm/memory/search/{query}", "GET")
        result = asyncio.run(search(query="zzznope"))
        assert result == []


# ======================================================================
# Logs
# ======================================================================

class TestSwarmLogs:

    def test_write_log(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        result = asyncio.run(ep(body=LogWrite(message="server started", level="info", agent_id="a1")))
        assert "id" in result
        assert result["level"] == "info"

    def test_list_logs(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="log one")))
        asyncio.run(create(body=LogWrite(message="log two")))
        asyncio.run(create(body=LogWrite(message="log three")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/logs", "GET")
        result = asyncio.run(list_ep())
        assert len(result) == 3

    def test_list_logs_filter_by_level(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="info msg", level="info")))
        asyncio.run(create(body=LogWrite(message="error msg", level="error")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/logs", "GET")
        errors = asyncio.run(list_ep(level="error"))
        assert len(errors) == 1
        assert errors[0]["level"] == "error"

    def test_list_logs_filter_by_agent(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="from a1", agent_id="a1")))
        asyncio.run(create(body=LogWrite(message="from a2", agent_id="a2")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/logs", "GET")
        a1_logs = asyncio.run(list_ep(agent_id="a1"))
        assert len(a1_logs) == 1
        assert a1_logs[0]["agent_id"] == "a1"

    def test_search_logs(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="connection timeout to database")))
        asyncio.run(create(body=LogWrite(message="startup complete")))

        search = _find_endpoint(swarm_router, "/api/swarm/logs/search/{query}", "GET")
        result = asyncio.run(search(query="timeout"))
        assert len(result) >= 1
        assert any("timeout" in r["message"] for r in result)

    def test_clear_logs_all(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="old log 1")))
        asyncio.run(create(body=LogWrite(message="old log 2")))

        clear = _find_endpoint(swarm_router, "/api/swarm/logs", "DELETE")
        result = asyncio.run(clear())
        assert result["deleted"] == 2

        # Verify empty
        list_ep = _find_endpoint(swarm_router, "/api/swarm/logs", "GET")
        assert asyncio.run(list_ep()) == []

    def test_clear_logs_before_timestamp(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        asyncio.run(create(body=LogWrite(message="old")))

        clear = _find_endpoint(swarm_router, "/api/swarm/logs", "DELETE")
        # Use a far-future timestamp so nothing is deleted
        result = asyncio.run(clear(before=9999999999.0))
        assert result["deleted"] == 0


# ======================================================================
# Events
# ======================================================================

class TestSwarmEvents:

    def test_publish_event(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        result = asyncio.run(ep(body=EventPublish(topic="deploy", source="ci", payload='{"version":"1.0"}')))
        assert result["topic"] == "deploy"
        assert "id" in result

    def test_list_events(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        asyncio.run(create(body=EventPublish(topic="t1")))
        asyncio.run(create(body=EventPublish(topic="t2")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/events", "GET")
        result = asyncio.run(list_ep())
        assert len(result) == 2

    def test_list_events_filter_by_topic(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        asyncio.run(create(body=EventPublish(topic="deploy")))
        asyncio.run(create(body=EventPublish(topic="alert")))

        list_ep = _find_endpoint(swarm_router, "/api/swarm/events", "GET")
        deploys = asyncio.run(list_ep(topic="deploy"))
        assert len(deploys) == 1
        assert deploys[0]["topic"] == "deploy"

    def test_clear_events_all(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        asyncio.run(create(body=EventPublish(topic="a")))
        asyncio.run(create(body=EventPublish(topic="b")))

        clear = _find_endpoint(swarm_router, "/api/swarm/events", "DELETE")
        result = asyncio.run(clear())
        assert result["deleted"] == 2

    def test_clear_events_before_timestamp(self, swarm_router):
        create = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        asyncio.run(create(body=EventPublish(topic="x")))

        clear = _find_endpoint(swarm_router, "/api/swarm/events", "DELETE")
        result = asyncio.run(clear(before=9999999999.0))
        assert result["deleted"] == 0


# ======================================================================
# Dashboard / Stats
# ======================================================================

class TestSwarmStats:

    def test_stats_empty_database(self, swarm_router):
        ep = _find_endpoint(swarm_router, "/api/swarm/stats", "GET")
        result = asyncio.run(ep())
        assert result["tasks"] == {}
        assert result["agents"] == {}
        assert result["total_logs"] == 0
        assert result["total_events"] == 0
        assert result["total_memory"] == 0

    def test_stats_with_populated_data(self, swarm_router):
        create_task = _find_endpoint(swarm_router, "/api/swarm/tasks", "POST")
        create_agent = _find_endpoint(swarm_router, "/api/swarm/agents", "POST")
        create_log = _find_endpoint(swarm_router, "/api/swarm/logs", "POST")
        create_event = _find_endpoint(swarm_router, "/api/swarm/events", "POST")
        set_memory = _find_endpoint(swarm_router, "/api/swarm/memory", "POST")

        t1 = asyncio.run(create_task(body=TaskCreate(title="t1")))
        asyncio.run(create_task(body=TaskCreate(title="t2")))
        update = _find_endpoint(swarm_router, "/api/swarm/tasks/{task_id}", "PATCH")
        asyncio.run(update(task_id=t1["id"], body=TaskUpdate(status="completed")))

        asyncio.run(create_agent(body=AgentRegister(name="a1")))
        asyncio.run(create_agent(body=AgentRegister(name="a2")))
        hb = _find_endpoint(swarm_router, "/api/swarm/agents/{agent_id}/heartbeat", "POST")
        a1_id = asyncio.run(_find_endpoint(swarm_router, "/api/swarm/agents", "GET")())[0]["id"]
        asyncio.run(hb(agent_id=a1_id, body=AgentHeartbeat(status="busy")))

        asyncio.run(create_log(body=LogWrite(message="l1")))
        asyncio.run(create_log(body=LogWrite(message="l2")))
        asyncio.run(create_log(body=LogWrite(message="l3")))

        asyncio.run(create_event(body=EventPublish(topic="e1")))
        asyncio.run(set_memory(body=MemorySet(key="k1", value="v1")))

        ep = _find_endpoint(swarm_router, "/api/swarm/stats", "GET")
        result = asyncio.run(ep())
        assert result["tasks"]["completed"] == 1
        assert result["tasks"]["pending"] == 1
        assert result["agents"]["busy"] == 1
        assert result["agents"]["online"] == 1
        assert result["total_logs"] == 3
        assert result["total_events"] == 1
        assert result["total_memory"] == 1


# ======================================================================
# Diagnostics routes
# ======================================================================

class TestDiagnosticsRoutes:

    def test_database_stats(self, diag_router):
        ep = _find_endpoint(diag_router, "/api/db/stats", "GET")
        result = asyncio.run(ep())
        assert result["tables"] == 5
        assert result["total_rows"] == 100

    def test_database_stats_error_returns_500(self, monkeypatch):
        """When get_detailed_stats raises, the endpoint returns 500."""
        core_db = types.ModuleType("core.database")

        def _boom():
            raise RuntimeError("db offline")

        core_db.get_detailed_stats = _boom
        monkeypatch.setitem(sys.modules, "core.database", core_db)

        rag_manager = MagicMock()
        rh = MagicMock()
        router = setup_diagnostics_routes(rag_manager, True, rh)
        ep = _find_endpoint(router, "/api/db/stats", "GET")
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ep())
        assert exc.value.status_code == 500

    def test_rag_stats_available(self, diag_router):
        ep = _find_endpoint(diag_router, "/api/rag/stats", "GET")
        result = asyncio.run(ep())
        assert result["chunks"] == 50

    def test_rag_stats_unavailable(self):
        router = setup_diagnostics_routes(None, rag_available=False, research_handler=MagicMock())
        ep = _find_endpoint(router, "/api/rag/stats", "GET")
        result = asyncio.run(ep())
        assert "error" in result
        assert "not available" in result["error"]

    def test_youtube_valid_url(self, diag_router):
        ep = _find_endpoint(diag_router, "/api/test/youtube", "GET")
        result = asyncio.run(ep(url="https://www.youtube.com/watch?v=abc123"))
        assert result["video_id"] == "abc123"
        assert result["transcript_success"] is True
        assert result["transcript_length"] > 0

    def test_youtube_invalid_url(self, monkeypatch):
        monkeypatch.setattr(diag_mod, "extract_youtube_id", MagicMock(return_value=None))
        rag_manager = MagicMock()
        router = setup_diagnostics_routes(rag_manager, True, MagicMock())
        ep = _find_endpoint(router, "/api/test/youtube", "GET")
        result = asyncio.run(ep(url="not-a-youtube-url"))
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_research_endpoint_success(self, diag_router):
        ep = _find_endpoint(diag_router, "/api/test-research", "POST")
        result = asyncio.run(ep(query="What is machine learning?"))
        assert result["status"] == "success"
        assert result["query"] == "What is machine learning?"
        assert result["result_length"] > 0

    def test_research_endpoint_error(self, monkeypatch):
        rh = MagicMock()
        rh.call_research_service = AsyncMock(side_effect=RuntimeError("service down"))
        router = setup_diagnostics_routes(MagicMock(), True, rh)

        # Need the youtube stub for import
        monkeypatch.setattr(diag_mod, "extract_youtube_id", MagicMock(return_value="x"))
        monkeypatch.setattr(diag_mod, "extract_transcript_async", AsyncMock(return_value={}))

        ep = _find_endpoint(router, "/api/test-research", "POST")
        result = asyncio.run(ep(query="test"))
        assert result["status"] == "error"
        assert "service down" in result["error"]
