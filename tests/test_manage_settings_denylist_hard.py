"""The global disabled_tools denylist must be a hard boundary for the agent.

``manage_settings`` used to expose ``enable_tool``, which let any agent turn
(admin-run, but reading arbitrary web pages, emails, and documents) remove
entries from ``settings.json:disabled_tools`` — the only global tool
permission mechanism the Settings > Agent Tools panel offers. A prompt
injection could therefore re-enable bash, email, or any other tool an admin
had switched off. Regression for issue #5523: the agent may still narrow the
denylist (``disable_tool``) and inspect it (``list_tools``), but widening it
is refused and left to a human in the Settings panel.
"""

import asyncio
import json

import pytest

import src.settings as settings_mod
from src.agent_tools.admin_tools import do_manage_settings


@pytest.fixture
def store(monkeypatch):
    state = {"disabled_tools": ["bash", "manage_memory", "web_search", "web_fetch"]}
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(state))

    def _save(s):
        state.clear()
        state.update(s)

    monkeypatch.setattr(settings_mod, "save_settings", _save)
    monkeypatch.setattr(
        settings_mod, "get_setting",
        lambda key, default=None: state.get(key, default),
    )
    return state


def _call(payload, owner="admin"):
    return asyncio.run(do_manage_settings(json.dumps(payload), owner=owner))


@pytest.mark.parametrize("tool", ["shell", "bash", "memory", "manage_memory", "search"])
def test_enable_tool_is_refused_and_denylist_unchanged(store, tool):
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool", "tool": tool})
    assert result["exit_code"] == 1, result
    assert "Settings" in result["error"]
    assert store["disabled_tools"] == before


def test_enable_tool_refused_without_tool_name(store):
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool"})
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


def test_enable_tool_refused_via_name_alias_argument(store):
    # The toggle branch also accepted `name` as the tool argument.
    before = list(store["disabled_tools"])
    result = _call({"action": "enable_tool", "name": "shell"})
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


def test_disable_tool_still_narrows_the_denylist(store):
    result = _call({"action": "disable_tool", "tool": "images"})
    assert result["exit_code"] == 0, result
    assert "generate_image" in store["disabled_tools"]
    # Nothing that was disabled before is re-enabled as a side effect.
    for name in ("bash", "manage_memory", "web_search", "web_fetch"):
        assert name in store["disabled_tools"]


def test_list_tools_still_reports_the_denylist(store):
    result = _call({"action": "list_tools"})
    assert result["exit_code"] == 0, result
    assert set(result["disabled"]) == {"bash", "manage_memory", "web_search", "web_fetch"}
    assert "Settings > Agent Tools" in result["response"]


@pytest.mark.parametrize("action", ["set", "delete", "reset"])
def test_disabled_tools_key_is_not_reachable_through_settings_actions(store, action):
    # Belt and braces: the free-form settings actions must not offer a second
    # path to the denylist (a `reset` would restore the default empty list).
    before = list(store["disabled_tools"])
    payload = {"action": action, "key": "disabled_tools"}
    if action == "set":
        payload["value"] = []
    result = _call(payload)
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


@pytest.mark.parametrize("action", ["set", "delete", "reset"])
def test_disabled_tools_stays_protected_if_added_to_defaults(store, monkeypatch, action):
    # The old rejection depended on disabled_tools not being in DEFAULT_SETTINGS.
    monkeypatch.setitem(settings_mod.DEFAULT_SETTINGS, "disabled_tools", [])
    before = list(store["disabled_tools"])
    result = _call({"action": action, "key": "disabled_tools", "value": []})
    assert result["exit_code"] == 1, result
    assert store["disabled_tools"] == before


@pytest.mark.parametrize("path", ["/api/tools", "api/tools", "/api/tools/", "/api/tools?source=chat"])
def test_app_api_cannot_replace_global_tool_policy(monkeypatch, path):
    import httpx
    from src.tools.system import do_app_api

    def no_loopback(*args, **kwargs):
        raise AssertionError("Tool policy write reached the HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", no_loopback)
    result = asyncio.run(do_app_api(json.dumps({
        "action": "call", "method": "post", "path": path, "body": {"disabled": []},
    }), owner="admin"))
    assert result["exit_code"] == 1
    assert "Settings > Agent Tools" in result["error"]


@pytest.mark.parametrize("internal", [True, False])
def test_tools_api_requires_human_admin_for_policy_changes(store, monkeypatch, internal):
    from types import SimpleNamespace
    from starlette.requests import Request
    from fastapi import HTTPException
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    import routes.model_routes as routes

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(routes, "_load_settings", lambda: dict(store))
    monkeypatch.setattr(routes, "_save_settings", lambda value: store.update(value))
    router = routes.setup_model_routes(SimpleNamespace())
    endpoint = next(r.endpoint for r in router.routes if r.path == "/api/tools" and "POST" in r.methods)
    body = endpoint.__annotations__["body"](disabled=[])
    headers = [(INTERNAL_TOOL_HEADER.lower().encode(), INTERNAL_TOOL_TOKEN.encode())] if internal else []
    request = Request({
        "type": "http", "method": "POST", "path": "/api/tools", "headers": headers,
        "state": {"current_user": "admin"},
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(
            is_configured=True, is_admin=lambda user: user == "admin",
        ))),
    })
    if internal:
        before = list(store["disabled_tools"])
        with pytest.raises(HTTPException) as error:
            endpoint(body, request)
        assert error.value.status_code == 403
        assert store["disabled_tools"] == before
    else:
        assert endpoint(body, request) == {"ok": True, "disabled": []}
        assert store["disabled_tools"] == []


def test_schema_no_longer_advertises_enable_tool():
    import src.agent_tools  # noqa: F401  (resolves the schema import cycle)
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    schema = next(
        t["function"] for t in FUNCTION_TOOL_SCHEMAS
        if (t.get("function") or {}).get("name") == "manage_settings"
    )
    actions = schema["parameters"]["properties"]["action"]["enum"]
    assert "enable_tool" not in actions
    assert "disable_tool" in actions and "list_tools" in actions
    assert "enable_tool" not in schema["description"]


@pytest.mark.parametrize("path", [
    "/api/mcp/servers/srv1/tools", "api/mcp/servers/srv1/tools", "/api/mcp/servers/srv1",
])
def test_app_api_cannot_change_mcp_tool_policy(monkeypatch, path):
    import httpx
    from src.tools.system import do_app_api

    def no_loopback(*args, **kwargs):
        raise AssertionError("MCP tool policy write reached the HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", no_loopback)
    result = asyncio.run(do_app_api(json.dumps({
        "action": "call", "method": "patch", "path": path, "body": {"disabled": []},
    }), owner="admin"))
    assert result["exit_code"] == 1
    assert "Settings > Integrations" in result["error"]


@pytest.mark.parametrize("internal", [True, False])
def test_mcp_tools_api_requires_human_admin_for_policy_changes(monkeypatch, internal):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from starlette.requests import Request
    from fastapi import HTTPException
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from routes.mcp import mcp_routes

    server = SimpleNamespace(id="srv1", disabled_tools=json.dumps(["send_email"]))
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = server
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(mcp_routes, "SessionLocal", lambda: session)
    router = mcp_routes.setup_mcp_routes(MagicMock())
    # setup_mcp_routes appends to the shared module router; take the latest.
    endpoint = [r.endpoint for r in router.routes if getattr(r, "name", None) == "update_disabled_tools"][-1]
    headers = [(b"content-type", b"application/json")]
    if internal:
        headers.append((INTERNAL_TOOL_HEADER.lower().encode(), INTERNAL_TOOL_TOKEN.encode()))

    async def receive():
        return {"type": "http.request", "body": b'{"disabled": []}', "more_body": False}

    request = Request({
        "type": "http", "method": "PATCH", "path": "/api/mcp/servers/srv1/tools", "headers": headers,
        "state": {"current_user": "admin"},
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(
            is_configured=True, is_admin=lambda user: user == "admin",
        ))),
    }, receive)
    if internal:
        with pytest.raises(HTTPException) as error:
            asyncio.run(endpoint("srv1", request))
        assert error.value.status_code == 403
        assert server.disabled_tools == json.dumps(["send_email"])
        session.commit.assert_not_called()
    else:
        assert asyncio.run(endpoint("srv1", request)) == {"id": "srv1", "disabled_count": 0}
        assert server.disabled_tools is None
        session.commit.assert_called_once()
