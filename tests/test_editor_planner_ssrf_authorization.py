import asyncio
import http.server
import json
import threading
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from core.database import SessionLocal, ModelEndpoint
from routes.editor_agent_routes import setup_editor_agent_routes
import src.editor_agent_model as eam


class TrackingRequestHandler(http.server.BaseHTTPRequestHandler):
    received_requests: List[Tuple[str, str, dict]] = []

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length) if length > 0 else b""
        try:
            body_json = json.loads(body.decode("utf-8"))
        except Exception:
            body_json = {}
        TrackingRequestHandler.received_requests.append((self.command, self.path, body_json))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"choices": [{"message": {"content": "ATTACKER_PLAN"}}]}')

    def log_message(self, format, *args):
        pass


@pytest.fixture
def local_listener():
    TrackingRequestHandler.received_requests = []
    server = http.server.HTTPServer(("127.0.0.1", 0), TrackingRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()
    server.server_close()


def create_mock_request(path: str, body: dict, user: str = "testuser", is_admin: bool = False):
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "app": MagicMock(),
    }
    req = Request(scope)
    auth_mgr = MagicMock()
    auth_mgr.is_admin.return_value = is_admin
    auth_mgr.is_configured = True
    req.app.state.auth_manager = auth_mgr

    async def mock_json():
        return body

    req.json = mock_json
    return req


SYNTHETIC_SCREENSHOT = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def test_ssrf_arbitrary_listener_receives_zero_requests(local_listener, monkeypatch):
    """Prove attacker-supplied agent_model URL receives 0 requests on planner routes."""
    router = setup_editor_agent_routes()
    plan_route = next(r for r in router.routes if r.path == "/api/editor/plan")
    agent_route = next(r for r in router.routes if r.path == "/api/editor/agent")

    async def _fake_wait(fut, timeout=None, **kwargs):
        return {"ok": True, "result": {"width": 100, "height": 100}}

    monkeypatch.setattr("routes.editor_agent_routes.asyncio.wait_for", _fake_wait)

    payload = {
        "prompt": "make it brighter",
        "screenshot": SYNTHETIC_SCREENSHOT,
        "agent_model": f"{local_listener}::attacker-model",
    }

    # Test /api/editor/plan
    req_plan = create_mock_request("/api/editor/plan", payload, user="alice", is_admin=False)
    with patch("routes.editor_agent_routes._user", return_value="alice"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=False):
        resp_plan = asyncio.run(plan_route.endpoint(req_plan))
    assert resp_plan.status_code == 200
    assert len(TrackingRequestHandler.received_requests) == 0

    # Test /api/editor/agent
    req_agent = create_mock_request("/api/editor/agent", payload, user="alice", is_admin=False)
    with patch("routes.editor_agent_routes._user", return_value="alice"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=False):
        resp_agent = asyncio.run(agent_route.endpoint(req_agent))
    assert resp_agent.status_code == 200
    assert len(TrackingRequestHandler.received_requests) == 0


def test_ssrf_admin_cannot_target_arbitrary_url(local_listener):
    """Admin access must not grant arbitrary outbound network destinations."""
    payload = {
        "prompt": "make it brighter",
        "screenshot": SYNTHETIC_SCREENSHOT,
        "agent_model": f"{local_listener}::attacker-model",
    }
    router = setup_editor_agent_routes()
    plan_route = next(r for r in router.routes if r.path == "/api/editor/plan")

    req = create_mock_request("/api/editor/plan", payload, user="admin_user", is_admin=True)
    with patch("routes.editor_agent_routes._user", return_value="admin_user"), \
         patch("src.tool_security.owner_is_admin_or_single_user", return_value=True):
        resp = asyncio.run(plan_route.endpoint(req))
    assert resp.status_code == 200
    assert len(TrackingRequestHandler.received_requests) == 0


def test_candidates_cloud_metadata_rejected():
    """Cloud metadata destination is rejected fail-closed."""
    cands = eam._candidates("user1", "http://169.254.169.254/latest/meta-data::cloud-meta")
    assert cands == []


def test_candidates_foreign_owned_endpoint_rejected():
    """Foreign-owned endpoint ID fails closed for a different user."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-foreign-test",
            name="Foreign Cluster",
            base_url="http://192.168.1.100:8000/v1",
            owner="victim_user",
            is_enabled=True,
            cached_models=json.dumps(["victim-model"]),
        )
        db.merge(ep)
        db.commit()

        # Attacker user query
        cands = eam._candidates("attacker_user", "ep-foreign-test::victim-model")
        assert cands == []
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-foreign-test").delete()
        db.commit()
        db.close()


def test_candidates_disabled_endpoint_rejected():
    """Disabled endpoint ID fails closed."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-disabled-test",
            name="Disabled Cluster",
            base_url="http://192.168.1.100:8000/v1",
            owner="user1",
            is_enabled=False,
            cached_models=json.dumps(["some-model"]),
        )
        db.merge(ep)
        db.commit()

        cands = eam._candidates("user1", "ep-disabled-test::some-model")
        assert cands == []
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-disabled-test").delete()
        db.commit()
        db.close()


def test_candidates_nonexistent_endpoint_rejected():
    """Nonexistent endpoint ID fails closed."""
    cands = eam._candidates("user1", "ep-nonexistent-9999::model1")
    assert cands == []


def test_candidates_forged_model_id_rejected():
    """Forged model ID on an authorized endpoint fails closed."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-auth-test",
            name="User Cluster",
            base_url="http://127.0.0.1:9099/v1",
            owner="user1",
            is_enabled=True,
            cached_models=json.dumps(["real-model"]),
        )
        db.merge(ep)
        db.commit()

        # Legitimate model passes
        valid_cands = eam._candidates("user1", "ep-auth-test::real-model")
        assert len(valid_cands) >= 1
        assert valid_cands[0]["model"] == "real-model"

        # Forged model fails closed
        forged_cands = eam._candidates("user1", "ep-auth-test::forged-attacker-model")
        assert forged_cands == []
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-auth-test").delete()
        db.commit()
        db.close()


def test_candidates_duplicate_model_ids_scoped_by_endpoint():
    """Duplicate model IDs across different endpoints do not collide or cross providers."""
    db = SessionLocal()
    try:
        ep1 = ModelEndpoint(
            id="ep-provider-1",
            name="Provider 1",
            base_url="http://127.0.0.1:9001/v1",
            owner="user1",
            is_enabled=True,
            cached_models=json.dumps(["shared-model-name"]),
        )
        ep2 = ModelEndpoint(
            id="ep-provider-2",
            name="Provider 2",
            base_url="http://127.0.0.1:9002/v1",
            owner="user1",
            is_enabled=True,
            cached_models=json.dumps(["shared-model-name"]),
        )
        db.merge(ep1)
        db.merge(ep2)
        db.commit()

        cands1 = eam._candidates("user1", "ep-provider-1::shared-model-name")
        assert len(cands1) >= 1
        assert cands1[0]["endpoint_id"] == "ep-provider-1"
        assert "9001" in cands1[0]["base"]

        cands2 = eam._candidates("user1", "ep-provider-2::shared-model-name")
        assert len(cands2) >= 1
        assert cands2[0]["endpoint_id"] == "ep-provider-2"
        assert "9002" in cands2[0]["base"]
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id.in_(["ep-provider-1", "ep-provider-2"])).delete()
        db.commit()
        db.close()


def test_candidates_authorized_shared_endpoint():
    """Shared endpoint (owner is NULL) is accessible to authenticated user."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-shared-test",
            name="Shared Org Cluster",
            base_url="http://127.0.0.1:9005/v1",
            owner=None,
            is_enabled=True,
            cached_models=json.dumps(["org-planner-model"]),
        )
        db.merge(ep)
        db.commit()

        cands = eam._candidates("any_user", "ep-shared-test::org-planner-model")
        assert len(cands) >= 1
        assert cands[0]["endpoint_id"] == "ep-shared-test"
        assert cands[0]["model"] == "org-planner-model"
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-shared-test").delete()
        db.commit()
        db.close()


def test_candidates_legacy_raw_url_override_verified_against_registered_db():
    """Legacy raw URL format resolves only when matching a registered DB endpoint."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-legacy-test",
            name="Legacy Registered",
            base_url="http://registered-server.internal:8000/v1",
            owner="user1",
            is_enabled=True,
            cached_models=json.dumps(["legacy-model"]),
        )
        db.merge(ep)
        db.commit()

        # Matching registered row resolves
        cands = eam._candidates("user1", "http://registered-server.internal:8000/v1::legacy-model")
        assert len(cands) >= 1
        assert cands[0]["endpoint_id"] == "ep-legacy-test"

        # Unregistered raw URL fails closed
        unreg_cands = eam._candidates("user1", "http://unregistered-server.internal:8000/v1::legacy-model")
        assert unreg_cands == []
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-legacy-test").delete()
        db.commit()
        db.close()


def test_candidates_unauthorized_override_does_not_silently_fallback(monkeypatch):
    """Unauthorized override fails closed rather than silently falling back to defaults."""
    monkeypatch.setattr(eam, "local_bases", lambda: ["http://default-local:8000/v1"])
    monkeypatch.setattr(eam, "local_model", lambda: "default-local-model")

    # Override is unauthorized/attacker -> MUST NOT fallback to default-local
    cands = eam._candidates("user1", "http://attacker-listener.com:9999/v1::attacker-model")
    assert cands == []


def test_editor_routes_unauthenticated_returns_401_when_auth_configured():
    """Unauthenticated requests must return 401 when authentication is enabled."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    router = setup_editor_agent_routes()
    app.include_router(router)

    auth_mgr = MagicMock()
    auth_mgr.is_configured = True
    app.state.auth_manager = auth_mgr

    with patch("src.auth_helpers._auth_disabled", return_value=False), \
         patch("src.auth_helpers.get_current_user", return_value=None):
        client = TestClient(app, raise_server_exceptions=False)
        resp_plan = client.post("/api/editor/plan", json={"prompt": "draw cat"})
        assert resp_plan.status_code == 401
        assert resp_plan.json().get("detail") == "Not authenticated"

        resp_agent = client.post("/api/editor/agent", json={"prompt": "draw cat"})
        assert resp_agent.status_code == 401
        assert resp_agent.json().get("detail") == "Not authenticated"


def test_candidates_empty_owner_cannot_query_foreign_private_endpoint():
    """Empty owner in multi-user mode cannot access private endpoints of other users."""
    db = SessionLocal()
    try:
        ep = ModelEndpoint(
            id="ep-private-victim",
            name="Victim Private Cluster",
            base_url="http://192.168.1.105:8000/v1",
            owner="victim_user",
            is_enabled=True,
            cached_models=json.dumps(["victim-secret-model"]),
        )
        db.merge(ep)
        db.commit()

        with patch("src.auth_helpers._auth_disabled", return_value=False):
            # None owner fails closed
            cands_none = eam._candidates(None, "ep-private-victim::victim-secret-model")
            assert cands_none == []

            # Empty string owner fails closed
            cands_empty = eam._candidates("", "ep-private-victim::victim-secret-model")
            assert cands_empty == []
    finally:
        db.query(ModelEndpoint).filter(ModelEndpoint.id == "ep-private-victim").delete()
        db.commit()
        db.close()


def test_editor_routes_api_token_without_chat_scope_returns_403():
    """Bearer API token missing 'chat' scope is rejected with 403."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.middleware("http")
    async def inject_token_middleware(request, call_next):
        request.state.api_token = True
        request.state.api_token_scopes = ["image", "read"]  # Missing "chat"
        request.state.api_token_owner = "alice"
        return await call_next(request)

    app.include_router(setup_editor_agent_routes())
    client = TestClient(app, raise_server_exceptions=False)

    resp_plan = client.post("/api/editor/plan", json={"prompt": "make it pop"})
    assert resp_plan.status_code == 403
    assert "chat" in resp_plan.json().get("detail", "")

    resp_agent = client.post("/api/editor/agent", json={"prompt": "make it pop"})
    assert resp_agent.status_code == 403
    assert "chat" in resp_agent.json().get("detail", "")


def test_editor_routes_api_token_with_chat_scope_resolves_owner_endpoint():
    """Bearer API token with 'chat' scope resolves the token owner's endpoints."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db = SessionLocal()
    try:
        alice_ep = ModelEndpoint(
            id="ep-alice-token",
            name="Alice Cluster",
            base_url="http://127.0.0.1:9098/v1",
            owner="alice",
            is_enabled=True,
            cached_models=json.dumps(["alice-model"]),
        )
        bob_ep = ModelEndpoint(
            id="ep-bob-token",
            name="Bob Cluster",
            base_url="http://127.0.0.1:9099/v1",
            owner="bob",
            is_enabled=True,
            cached_models=json.dumps(["bob-model"]),
        )
        db.merge(alice_ep)
        db.merge(bob_ep)
        db.commit()

        app = FastAPI()

        @app.middleware("http")
        async def inject_token_middleware(request, call_next):
            request.state.api_token = True
            request.state.api_token_scopes = ["chat"]
            request.state.api_token_owner = "alice"
            return await call_next(request)

        app.include_router(setup_editor_agent_routes())
        client = TestClient(app, raise_server_exceptions=False)

        # Alice's token querying Alice's endpoint resolves
        cands = eam._candidates("alice", "ep-alice-token::alice-model")
        assert len(cands) == 1
        assert cands[0]["endpoint_id"] == "ep-alice-token"

        # Alice's token querying Bob's endpoint fails closed
        cands_bob = eam._candidates("alice", "ep-bob-token::bob-model")
        assert cands_bob == []

        # Route-level check: calling /api/editor/plan with Alice's token targeting Bob's endpoint
        # falls back and does NOT route to Bob
        resp = client.post(
            "/api/editor/plan",
            json={"prompt": "enhance", "agent_model": "ep-bob-token::bob-model"}
        )
        assert resp.status_code == 200
        # Should report fallback or default, never bob's endpoint
        assert resp.json().get("agent_model") != "ep-bob-token::bob-model"
    finally:
        db.query(ModelEndpoint).filter(
            ModelEndpoint.id.in_(["ep-alice-token", "ep-bob-token"])
        ).delete()
        db.commit()
        db.close()
