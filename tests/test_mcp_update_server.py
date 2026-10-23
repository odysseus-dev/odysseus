"""Editing an MCP server keeps its id (#6328).

Saved servers could not be edited, so fixing even a typo in the name meant
delete + re-add, which gave the server a new id. Tool names are
mcp__<id>__<tool>, so every setting that referred to the old id
(ODYSSEUS_EXTRA_ALWAYS_TOOLS, ODYSSEUS_FORCE_DISABLED_TOOLS, disabled_tools)
silently stopped matching.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.database as db
from routes.mcp import mcp_routes


@pytest.fixture
def harness(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(mcp_routes, "SessionLocal", factory)
    monkeypatch.setattr(mcp_routes, "require_admin", lambda request: None)
    manager = MagicMock()
    manager.connect_server = AsyncMock(return_value=True)
    manager.disconnect_server = AsyncMock()
    manager.get_server_status = MagicMock(return_value={"status": "connected", "tool_count": 2})
    router = mcp_routes.setup_mcp_routes(manager)
    # setup_mcp_routes appends to a shared router; take the routes just added.
    update = [r for r in router.routes if getattr(r, "name", None) == "update_server"][-1].endpoint
    with factory() as session:
        session.add(db.McpServer(id="abc12345", name="Filesytem", transport="stdio",
            command="npx", args=json.dumps(["-y", "server-filesystem"]),
            env=json.dumps({"TOKEN": "secret"}), is_enabled=True,
            disabled_tools=json.dumps(["write_file"])))
        session.add(db.McpServer(id="http0001", name="Remote", transport="http",
            url="https://old.example/mcp", is_enabled=False))
        session.commit()

    def call(server_id, **fields):
        form = {"name": None, "command": None, "args": None, "env": None, "url": None, **fields}
        return asyncio.run(update(server_id=server_id, request=None, **form))

    def row(server_id):
        with factory() as session:
            return session.get(db.McpServer, server_id)

    return call, row, manager


def test_rename_keeps_id_and_everything_else(harness):
    call, row, manager = harness

    result = call("abc12345", name="Filesystem")

    assert result["id"] == "abc12345"
    srv = row("abc12345")
    assert srv.name == "Filesystem"
    assert json.loads(srv.args) == ["-y", "server-filesystem"]
    assert json.loads(srv.env) == {"TOKEN": "secret"}
    assert json.loads(srv.disabled_tools) == ["write_file"]
    manager.disconnect_server.assert_awaited_once_with("abc12345")
    assert manager.connect_server.await_args.kwargs["server_id"] == "abc12345"
    assert manager.connect_server.await_args.kwargs["name"] == "Filesystem"


def test_edit_stdio_command_args_and_env_reconnects_with_new_config(harness):
    call, row, manager = harness

    call("abc12345", command="uvx", args='["mcp-server-fs", "/data"]', env='{"TOKEN": "new"}')

    srv = row("abc12345")
    assert (srv.command, json.loads(srv.args), json.loads(srv.env)) == (
        "uvx", ["mcp-server-fs", "/data"], {"TOKEN": "new"})
    kwargs = manager.connect_server.await_args.kwargs
    assert (kwargs["command"], kwargs["args"], kwargs["env"]) == (
        "uvx", ["mcp-server-fs", "/data"], {"TOKEN": "new"})


@pytest.mark.parametrize("fields", [
    {"name": "  "},
    {"command": ""},
    {"args": "not json"},
    {"args": '{"a": 1}'},
    {"env": "not json"},
    {"env": '["x"]'},
])
def test_invalid_edits_are_rejected_and_nothing_changes(harness, fields):
    call, row, manager = harness

    with pytest.raises(HTTPException) as exc:
        call("abc12345", **fields)

    assert exc.value.status_code == 400
    srv = row("abc12345")
    assert srv.name == "Filesytem"
    assert json.loads(srv.env) == {"TOKEN": "secret"}
    manager.connect_server.assert_not_awaited()


def test_edit_url_of_disabled_server_does_not_connect(harness):
    call, row, manager = harness

    result = call("http0001", url="https://new.example/mcp")

    assert row("http0001").url == "https://new.example/mcp"
    assert result["connected"] is False
    manager.connect_server.assert_not_awaited()


def test_unknown_server_is_404(harness):
    call, _, _ = harness
    with pytest.raises(HTTPException) as exc:
        call("missing", name="x")
    assert exc.value.status_code == 404
