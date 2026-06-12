import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from routes.n8n_routes import setup_n8n_routes
from routes.openclaw_n8n_routes import setup_openclaw_n8n_routes
from src.n8n_client import N8nClient
from src.event_store import EventStore

# Mock dependencies
@pytest.fixture
def mock_n8n_client():
    with patch("routes.n8n_routes.N8nClient") as mock_n8n, patch("routes.openclaw_n8n_routes.N8nClient") as mock_openclaw_n8n:
        client = MagicMock()
        client.configured = True
        client.health = AsyncMock(return_value={"configured": True, "status": "ok", "http_status": 200})
        client.list_workflows = AsyncMock(return_value=[{"id": "1", "name": "Test Workflow"}])
        client.list_executions = AsyncMock(return_value=[
            {
                "id": "exec-1", 
                "workflowId": "wf-1", 
                "status": "error",
                "error": {"message": "Node failed"},
                "workflowData": {"name": "Test Workflow"}
            }
        ])
        client.get_failed_executions_summary = AsyncMock(return_value={
            "configured": True,
            "status": "error",
            "failed_count": 1,
            "executions": [{"id": "exec-1", "workflowId": "wf-1", "status": "error"}]
        })
        
        mock_n8n.return_value = client
        mock_openclaw_n8n.return_value = client
        yield client

@pytest.fixture
def mock_event_store(tmp_path):
    with patch("routes.n8n_routes.EventStore") as mock_store, patch("routes.openclaw_n8n_routes.EventStore") as mock_openclaw_store:
        store = MagicMock()
        
        def record_event_side_effect(**kwargs):
            event = kwargs.copy()
            event["id"] = "test-event-id"
            return event
            
        store.record_event.side_effect = record_event_side_effect
        mock_store.return_value = store
        mock_openclaw_store.return_value = store
        yield store

@pytest.fixture
def client_with_scopes(monkeypatch):
    def _mock_require_user(*args, **kwargs):
        return "alice"
        
    monkeypatch.setattr("src.auth_helpers.require_user", _mock_require_user)
    
    def _make_client(scopes):
        def _mock_scope_owner(request, allowed):
            if not getattr(request.state, 'api_token', False):
                return "alice"
            token_scopes = set(getattr(request.state, 'api_token_scopes', []) or [])
            if not token_scopes.intersection(allowed):
                from fastapi import HTTPException
                raise HTTPException(403, f"missing scope")
            return "alice"
            
        monkeypatch.setattr("routes.n8n_routes._scope_owner", _mock_scope_owner)
        monkeypatch.setattr("routes.openclaw_n8n_routes._scope_owner", _mock_scope_owner)
        
        class MockAuthMiddleware:
            def __init__(self, app):
                self.app = app
            async def __call__(self, scope, receive, send):
                if scope["type"] != "http":
                    return await self.app(scope, receive, send)
                scope["state"] = {"api_token": True, "api_token_scopes": scopes, "api_token_owner": "alice"}
                return await self.app(scope, receive, send)
        
        # Testing FastAPI with Starlette TestClient
        test_app = FastAPI()
        test_app.add_middleware(MockAuthMiddleware)
        test_app.include_router(setup_n8n_routes())
        test_app.include_router(setup_openclaw_n8n_routes())
        
        return TestClient(test_app)
        
    return _make_client

@pytest.mark.asyncio
async def test_missing_base_url_degraded_state():
    with patch.dict("os.environ", {"N8N_BASE_URL": ""}):
        client = N8nClient()
        assert client.configured is False
        health = await client.health()
        assert health == {"configured": False, "status": "unknown"}
        
        workflows = await client.list_workflows()
        assert workflows == []

def test_scope_enforcement_n8n_read(client_with_scopes, mock_n8n_client):
    c = client_with_scopes(["wrong:scope"])
    resp = c.get("/api/n8n/health")
    assert resp.status_code == 403
    
    c_valid = client_with_scopes(["n8n:read"])
    resp = c_valid.get("/api/n8n/health")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True

def test_scope_enforcement_n8n_events(client_with_scopes, mock_n8n_client, mock_event_store):
    c = client_with_scopes(["n8n:read"]) # missing n8n:events
    resp = c.post("/api/n8n/executions/record-events")
    assert resp.status_code == 403
    
    c_valid = client_with_scopes(["n8n:events"])
    resp = c_valid.post("/api/n8n/executions/record-events")
    assert resp.status_code == 200

def test_openclaw_n8n_health(client_with_scopes, mock_n8n_client):
    c = client_with_scopes(["n8n:read"])
    resp = c.get("/api/openclaw/n8n/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["overall_status"] == "ok"
    assert "n8n is reachable and healthy" in data["message"]

def test_openclaw_n8n_failures(client_with_scopes, mock_n8n_client):
    c = client_with_scopes(["n8n:read"])
    resp = c.get("/api/openclaw/n8n/failures")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["failures"]) == 1
    assert data["failures"][0]["id"] == "exec-1"

def test_openclaw_n8n_record_events(client_with_scopes, mock_n8n_client, mock_event_store):
    c = client_with_scopes(["n8n:events"])
    resp = c.post("/api/openclaw/n8n/failures/record")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["events"]) == 1
    
    event = data["events"][0]
    assert event["title"] == "n8n workflow failed"
    assert "Test Workflow" in event["summary"]
    assert "exec-1" in event["summary"]
    
    # check no destructive verbs
    actions = event["suggested_actions"]
    for forbidden in ["restart", "shell", "exec", "retry", "delete"]:
        assert forbidden not in actions
        
    assert "ack" in actions
    assert "investigate" in actions
    assert "view_workflow" in actions

def test_event_dedupe_key_by_workflow_id(client_with_scopes, mock_n8n_client, mock_event_store):
    c = client_with_scopes(["n8n:events"])
    resp = c.post("/api/n8n/executions/record-events")
    assert resp.status_code == 200
    
    mock_event_store.record_event.assert_called_once()
    call_args = mock_event_store.record_event.call_args[1]
    assert call_args["dedupe_key"] == "n8n:wf-1:failed"
    assert call_args["source"] == "n8n"
