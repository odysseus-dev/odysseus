"""Deleting a task must allow its own calendar loopback request to run."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import routes.task_routes as tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [True, False])
async def test_task_delete_services_calendar_loopback_without_blocking(monkeypatch, tmp_path, marker):
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(tasks, "SessionLocal", sessions)
    requests = []

    async def respond(reader, writer):
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode()
            method, target, _ = header.split("\r\n", 1)[0].split(" ")
            requests.append((method, target, header))
            if target == "/api/calendar/calendars":
                payload = {"calendars": [{"name": "Cookbook", "id": "cookbook"}]}
            elif method == "GET":
                payload = {"events": [{"summary": "Serve: fixture", "uid": "fixture::occurrence"},
                                      {"summary": "Unrelated", "uid": "keep"}]}
            else:
                payload = {"ok": True}
            data = json.dumps(payload).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " +
                         str(len(data)).encode() + b"\r\n\r\n" + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(tasks, "internal_api_base", lambda: f"http://127.0.0.1:{port}")
    with sessions() as db:
        db.add(database.ScheduledTask(
            id="scheduled", owner="alice", name="Serve: fixture", task_type="action",
            action="cookbook_serve", prompt=json.dumps({"cookbook_event_uid": "fixture"}) if marker else "{}",
            trigger_type="webhook", status="active", output_target="session",
        ))
        db.commit()
    router = tasks.setup_task_routes(MagicMock())
    delete = next(route.endpoint for route in router.routes
                  if route.path == "/api/tasks/{task_id}" and "DELETE" in route.methods)
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    try:
        result = await asyncio.wait_for(delete(request, "scheduled"), timeout=3)
        assert result == {"ok": True}
        assert [(m, p) for m, p, _ in requests if m == "DELETE"] == [
            ("DELETE", "/api/calendar/events/fixture"),
        ]
        assert all("X-Odysseus-Owner: alice" in header for _, _, header in requests)
        with sessions() as db:
            assert db.get(database.ScheduledTask, "scheduled") is None
    finally:
        server.close()
        await server.wait_closed()
        engine.dispose()
