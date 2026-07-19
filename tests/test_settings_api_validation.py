import json
from unittest.mock import patch, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.auth_routes import setup_auth_routes

def test_settings_budget_api_roundtrip(monkeypatch):
    # Mock settings storage to avoid hitting real data/settings.json
    store = {"agent_input_token_budget": 0}
    def mock_load(): return dict(store)
    def mock_save(s): store.update(s)
    
    import routes.auth_routes as ar
    monkeypatch.setattr(ar, "_load_settings", mock_load)
    monkeypatch.setattr(ar, "_save_settings", mock_save)
    
    # Mock auth manager
    class DummyAuthManager:
        def is_admin(self, user): return True
        def get_username_for_token(self, token): return "admin"
        
    auth_manager = DummyAuthManager()
    router = setup_auth_routes(auth_manager)
    
    app = FastAPI()
    app.include_router(router)
    
    # Monkeypatch the internal auth getter so we are logged in as admin
    # The router uses a closure `_get_current_user` inside `setup_auth_routes`
    # Instead we'll just set a cookie that our dummy manager resolves
    client = TestClient(app)
    client.cookies.set("odysseus_session", "dummy_token")
    
    # Test 1: Set to explicit cap (e.g. 5000)
    response = client.post("/api/auth/settings", json={"agent_input_token_budget": 5000})
    assert response.status_code == 200
    assert store["agent_input_token_budget"] == 5000
    
    # Test 2: Set to -1 (auto)
    response = client.post("/api/auth/settings", json={"agent_input_token_budget": -1})
    assert response.status_code == 200
    assert store["agent_input_token_budget"] == -1
    
    # Test 3: Set to 0 (disabled)
    response = client.post("/api/auth/settings", json={"agent_input_token_budget": 0})
    assert response.status_code == 200
    assert store["agent_input_token_budget"] == 0
    
    # Test 4: Too low is clamped to -1
    response = client.post("/api/auth/settings", json={"agent_input_token_budget": -500})
    assert response.status_code == 200
    assert store["agent_input_token_budget"] == -1
