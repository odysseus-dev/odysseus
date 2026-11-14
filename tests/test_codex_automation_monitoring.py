"""Regression tests for owner-scoped Codex automation monitoring."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.database as database
from routes import codex_routes
from tests.helpers.sqlite_db import make_temp_sqlite


def _request(*, scopes=None, owner="alice", api_token=True):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=api_token,
            api_token_scopes=scopes or [],
            api_token_owner=owner,
        ),
    )


def _endpoint():
    router = codex_routes.setup_codex_routes()
    for route in router.routes:
        if (
            getattr(route, "path", None)
            == "/api/codex/automation-monitoring"
            and "GET" in getattr(route, "methods", set())
        ):
            return route.endpoint
    raise AssertionError("Automation monitoring endpoint not found")


@pytest.fixture
def task_db(monkeypatch):
    factory, engine, tmpfile = make_temp_sqlite(database.Base.metadata)
    monkeypatch.setattr(codex_routes, "SessionLocal", factory)

    try:
        yield factory
    finally:
        engine.dispose()
        tmpfile.close()
        Path(tmpfile.name).unlink(missing_ok=True)


def _add_task(factory, *, task_id, owner, next_run, name=None):
    db = factory()
    try:
        db.add(
            database.ScheduledTask(
                id=task_id,
                owner=owner,
                name=name or task_id,
                status="active",
                next_run=next_run,
                last_run=None,
                run_count=0,
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.mark.parametrize("scope", ["todos:read", "todos:write"])
def test_monitoring_accepts_each_existing_todo_read_scope(task_db, scope):
    endpoint = _endpoint()
    result = asyncio.run(
        endpoint(request=_request(scopes=[scope]))
    )
    assert result == {"tasks": [], "total": 0, "limit": 50, "offset": 0}


def test_monitoring_rejects_missing_scope(task_db):
    endpoint = _endpoint()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint(request=_request(scopes=["unrelated:scope"]))
        )

    assert exc.value.status_code == 403


def test_monitoring_rejects_missing_token_owner(task_db):
    endpoint = _endpoint()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            endpoint(request=_request(scopes=["todos:read"], owner=None))
        )

    assert exc.value.status_code == 403


def test_monitoring_isolates_owners_orders_tasks_and_limits_fields(
    task_db,
):
    endpoint = _endpoint()
    now = datetime(2026, 11, 11, 12, 0, 0)

    _add_task(
        task_db,
        task_id="alice-later",
        owner="alice",
        next_run=now + timedelta(hours=2),
    )
    _add_task(
        task_db,
        task_id="bob-task",
        owner="bob",
        next_run=now,
    )
    _add_task(
        task_db,
        task_id="alice-earlier",
        owner="alice",
        next_run=now + timedelta(hours=1),
    )

    result = asyncio.run(
        endpoint(request=_request(scopes=["todos:read"]))
    )

    assert [task["id"] for task in result["tasks"]] == [
        "alice-earlier",
        "alice-later",
    ]

    expected_fields = {
        "id",
        "name",
        "status",
        "next_run",
        "last_run",
        "run_count",
    }

    for task in result["tasks"]:
        assert set(task) == expected_fields

    assert all(
        task["id"] != "bob-task"
        for task in result["tasks"]
    )


def test_monitoring_cookie_session_uses_require_user(
    task_db, monkeypatch
):
    endpoint = _endpoint()
    request = _request(api_token=False)
    calls = []

    def fake_require_user(req):
        calls.append(req)
        return "alice"

    monkeypatch.setattr(
        codex_routes,
        "require_user",
        fake_require_user,
    )

    result = asyncio.run(endpoint(request=request))

    assert result == {"tasks": [], "total": 0, "limit": 50, "offset": 0}
    assert calls == [request]


def test_monitoring_closes_database_session_when_query_fails(
    monkeypatch,
):
    class FailingSession:
        closed = False

        def query(self, model):
            raise RuntimeError("simulated database failure")

        def close(self):
            self.closed = True

    session = FailingSession()
    monkeypatch.setattr(
        codex_routes,
        "SessionLocal",
        lambda: session,
    )

    endpoint = _endpoint()

    with pytest.raises(RuntimeError, match="simulated database failure"):
        asyncio.run(
            endpoint(request=_request(scopes=["todos:read"]))
        )

    assert session.closed is True



def test_monitoring_paginates_with_stable_order_and_total(task_db):
    endpoint = _endpoint()
    now = datetime(2026, 11, 11, 12, 0, 0)

    for task_id in ["task-c", "task-a", "task-b"]:
        _add_task(
            task_db,
            task_id=task_id,
            owner="alice",
            next_run=now,
        )

    _add_task(
        task_db,
        task_id="other-owner",
        owner="bob",
        next_run=now,
    )

    result = asyncio.run(
        endpoint(
            request=_request(scopes=["todos:read"]),
            limit=2,
            offset=1,
        )
    )

    assert result["total"] == 3
    assert result["limit"] == 2
    assert result["offset"] == 1
    assert [task["id"] for task in result["tasks"]] == [
        "task-b",
        "task-c",
    ]


def test_monitoring_clamps_pagination_parameters(task_db):
    endpoint = _endpoint()

    for index in range(3):
        _add_task(
            task_db,
            task_id=f"task-{index}",
            owner="alice",
            next_run=datetime(2026, 11, 11, 12, index, 0),
        )

    result = asyncio.run(
        endpoint(
            request=_request(scopes=["todos:read"]),
            limit=500,
            offset=-5,
        )
    )

    assert result["total"] == 3
    assert result["limit"] == 200
    assert result["offset"] == 0
    assert len(result["tasks"]) == 3
