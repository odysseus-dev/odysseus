"""Regression test for issue #6211: a malformed Args value on the "Add MCP
Server" form must not be silently discarded into an empty argv.

routes/mcp/mcp_routes.py's add_server() wrapped json.loads(args) in a bare
except that fell back to `[]`, so a non-JSON Args value registered the
server as "Connected" while forwarding no arguments to the spawned stdio
subprocess at all, with no error surfaced anywhere.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from routes.mcp import mcp_routes


class _FakeSession:
    """Stands in for core.database.SessionLocal(); add_server only adds+commits."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def close(self):
        pass


def _add_server(monkeypatch):
    """Register add_server on the shared module-level router and return the
    freshly-added route's raw endpoint function, bypassing HTTP/Form parsing
    (require_admin is the only other thing the function touches via `request`).

    Callers must pass every Form(...) parameter add_server reads past the args
    check (url, oauth_file, oauth_config): calling the endpoint directly skips
    FastAPI's dependency resolution, so an omitted one arrives as the Form
    marker object itself rather than its declared default, and later code
    (e.g. `if oauth_file:`) reads that marker as truthy.
    """
    monkeypatch.setattr(mcp_routes, "require_admin", lambda request: None)
    manager = MagicMock()
    manager.connect_server = AsyncMock(return_value=True)
    manager.get_server_status = MagicMock(return_value={"status": "connected", "tool_count": 1})
    router = mcp_routes.setup_mcp_routes(manager)
    # setup_mcp_routes appends new APIRoute objects to the shared router on
    # every call, so take the LAST "add_server" route: the one just registered
    # with our fake manager, not an earlier registration from importing app.py.
    route = [r for r in router.routes if getattr(r, "name", None) == "add_server"][-1]
    return route.endpoint, manager


def test_add_server_rejects_malformed_args_instead_of_defaulting(monkeypatch):
    add_server, manager = _add_server(monkeypatch)
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: (_ for _ in ()).throw(
        AssertionError("must not reach the DB when args is rejected")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_server(
            request=None,
            name="filesystem",
            transport="stdio",
            command="mcp-server-filesystem",
            args="/app/data/jarvis-files",  # the exact value from issue #6211
            env="{}",
            url=None,
            oauth_file=None,
            oauth_config=None,
        ))

    assert exc.value.status_code == 400
    manager.connect_server.assert_not_called()


def test_add_server_still_accepts_valid_json_args(monkeypatch):
    add_server, manager = _add_server(monkeypatch)
    fake_session = _FakeSession()
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: fake_session)

    result = asyncio.run(add_server(
        request=None,
        name="filesystem",
        transport="stdio",
        command="mcp-server-filesystem",
        args=json.dumps(["/app/data/jarvis-files"]),
        env="{}",
        url=None,
        oauth_file=None,
        oauth_config=None,
    ))

    assert result["connected"] is True
    manager.connect_server.assert_awaited_once()
    assert manager.connect_server.call_args.kwargs["args"] == ["/app/data/jarvis-files"]
    assert fake_session.added[0].args == json.dumps(["/app/data/jarvis-files"])


def test_add_server_rejects_valid_json_args_that_is_not_a_list(monkeypatch):
    """Valid JSON that is not a list (e.g. args=5) must not reach
    StdioServerParameters(args=5), which raises an unhandled TypeError when
    the error formatter later does " ".join([command, *args])."""
    add_server, manager = _add_server(monkeypatch)
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: (_ for _ in ()).throw(
        AssertionError("must not reach the DB when args has the wrong shape")))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_server(
            request=None,
            name="filesystem",
            transport="stdio",
            command="mcp-server-filesystem",
            args="5",
            env="{}",
            url=None,
            oauth_file=None,
            oauth_config=None,
        ))

    assert exc.value.status_code == 400
    manager.connect_server.assert_not_called()


def test_add_server_still_defaults_empty_args_to_empty_list(monkeypatch):
    """No behavior change for the common case of an empty Args field."""
    add_server, manager = _add_server(monkeypatch)
    fake_session = _FakeSession()
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: fake_session)

    result = asyncio.run(add_server(
        request=None,
        name="no-args-server",
        transport="stdio",
        command="some-command",
        args="",
        env="{}",
        url=None,
        oauth_file=None,
        oauth_config=None,
    ))

    assert result["connected"] is True
    assert manager.connect_server.call_args.kwargs["args"] == []
