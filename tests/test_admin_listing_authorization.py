"""Real admin gates must run before MCP and diagnostic data access."""

import builtins
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from core.auth import AuthManager
from routes.auth_routes import SESSION_COOKIE
from tests.helpers.database import disposable_database


@pytest.fixture(scope="module")
def auth_configuration(tmp_path_factory):
    """Hash real fixture passwords once; only immutable bytes are reused."""
    path = tmp_path_factory.mktemp("admin-auth-seed") / "auth.json"
    auth = AuthManager(str(path))
    for user, admin in (("member", False), ("admin", True)):
        assert auth.create_user(user, "disposable-test-password", is_admin=admin)
    return path.read_bytes()


@pytest.fixture
def authenticated_app(tmp_path, monkeypatch, auth_configuration):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    path = tmp_path / "auth.json"
    path.write_bytes(auth_configuration)
    auth = AuthManager(str(path))
    app = FastAPI()
    app.state.auth_manager = auth

    @app.middleware("http")
    async def fixture_session(request, call_next):
        # Supply the authenticated principal, without replacing require_admin.
        request.state.current_user = auth.get_username_for_token(request.cookies.get(SESSION_COOKIE))
        return await call_next(request)

    def client_for(user):
        client = TestClient(app)
        if user != "anonymous":
            token = auth.create_session(user, "disposable-test-password")
            assert token
            client.cookies.set(SESSION_COOKIE, token)
        return client

    return app, client_for


@pytest.mark.parametrize("user", ["anonymous", "member", "admin"])
@pytest.mark.parametrize("path", ["/servers", "/tools", "/servers/private-server/tools"])
def test_mcp_listing_requires_real_admin_before_data_access(
    tmp_path, monkeypatch, authenticated_app, user, path,
):
    from core.database import McpServer
    from routes.mcp import mcp_routes

    app, client_for = authenticated_app
    with disposable_database(tmp_path) as factory:
        with factory() as db:
            db.add(McpServer(
                id="private-server", name="private-config-marker", transport="stdio",
                command="fixture-only", args="[]", env=json.dumps({"FIXTURE": "private-config-marker"}),
            ))
            db.commit()
        session = Mock(wraps=factory)
        manager = SimpleNamespace(
            get_server_status=Mock(return_value={"status": "connected", "tool_count": 1}),
            get_all_tools=Mock(return_value=[{
                "server_id": "private-server", "name": "private-tool", "description": "private-config-marker",
            }]),
        )
        # setup_mcp_routes otherwise appends to a shared process-global router.
        monkeypatch.setattr(mcp_routes, "router", APIRouter(prefix="/api/mcp"))
        monkeypatch.setattr(mcp_routes, "SessionLocal", session)
        app.include_router(mcp_routes.setup_mcp_routes(manager))
        with client_for(user) as client:
            response = client.get("/api/mcp" + path)
        if user == "admin":
            assert response.status_code == 200
            assert "private-config-marker" in response.text
            payload = response.json()
            if path == "/servers":
                assert payload[0]["env"] == {"FIXTURE": "private-config-marker"}
            else:
                assert payload[0]["name"] == "private-tool"
        else:
            assert response.status_code == 403
            assert response.json() == {"detail": "Admin only"}
            assert "private-config-marker" not in response.text
            session.assert_not_called()
            manager.get_server_status.assert_not_called()
            manager.get_all_tools.assert_not_called()


@pytest.mark.parametrize("user", ["anonymous", "member", "admin"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/db/stats"),
    ("GET", "/api/rag/stats"),
    ("GET", "/api/test/youtube?url=https://youtu.be/fixture"),
    ("POST", "/api/test-research"),
    ("GET", "/api/diagnostics/services"),
    ("GET", "/api/diagnostics/logs"),
])
def test_diagnostics_require_real_admin_before_sensitive_work(
    tmp_path, monkeypatch, authenticated_app, user, method, path,
):
    import core.database as database
    import src.service_health as health
    from routes import diagnostics_routes as routes

    app, client_for = authenticated_app
    marker = "private-diagnostics-marker"
    stats = Mock(return_value={"marker": marker})
    rag = SimpleNamespace(get_stats=Mock(return_value={"marker": marker}))
    research = SimpleNamespace(call_research_service=AsyncMock(return_value=marker))
    extract_id = Mock(return_value="fixture")
    transcript = AsyncMock(return_value={"success": True, "transcript": marker})
    collect = AsyncMock(return_value={"marker": marker})
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(marker + "\n", encoding="utf-8")
    read_log = Mock(wraps=builtins.open)
    monkeypatch.setattr(database, "get_detailed_stats", stats)
    monkeypatch.setattr(routes, "extract_youtube_id", extract_id)
    monkeypatch.setattr(routes, "extract_transcript_async", transcript)
    monkeypatch.setattr(health, "collect_service_health", collect)
    monkeypatch.setattr(routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "open", read_log, raising=False)
    app.include_router(routes.setup_diagnostics_routes(rag, True, research))
    with client_for(user) as client:
        response = client.request(method, path)
    if user == "admin":
        assert response.status_code == 200
        assert marker in response.text
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "Admin only"}
        assert marker not in response.text
        for operation in (stats, rag.get_stats, research.call_research_service,
                          extract_id, transcript, collect, read_log):
            operation.assert_not_called()


@pytest.mark.parametrize("instance", [0, 1])
def test_authenticated_fixture_has_private_users_sessions_and_file(authenticated_app, instance):
    app, client_for = authenticated_app
    auth = app.state.auth_manager
    assert set(auth.users) == {"member", "admin"}
    assert not auth._sessions
    with client_for("member") as client:
        assert auth.get_username_for_token(client.cookies.get(SESSION_COOKIE)) == "member"
    assert auth.create_user("fixture-local", "disposable-test-password")
    # Both parameter cases mutate their own file. Any shared manager, mutable
    # users/sessions or written-back seed makes the next case fail, in any order.
    assert "fixture-local" in auth.users
