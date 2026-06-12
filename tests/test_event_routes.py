import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.event_store import EventStore
from routes.event_routes import setup_event_routes
from routes.homelab_routes import setup_homelab_routes

def _request(*, api_token=True, scopes=None, owner="alice", current_user="browser-user"):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state, app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))

def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")

@pytest.fixture
def mock_event_store(monkeypatch, tmp_path):
    store_file = str(tmp_path / "homelab_events.json")
    monkeypatch.setattr("src.event_store.EVENTS_FILE", store_file)
    monkeypatch.setattr("routes.event_routes.EventStore", lambda: EventStore(store_file))
    monkeypatch.setattr("routes.homelab_routes.EventStore", lambda: EventStore(store_file))
    return EventStore(store_file)

@pytest.mark.asyncio
async def test_event_creation_from_health_check(mock_event_store, monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")

    def mock_load_services():
        return [{"name": "pihole", "container": "pihole_container"}]
        
    async def mock_check_service_health(srv):
        return {"name": "pihole", "status": "error", "container_status": "exited"}

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    # Missing events:write scope
    with pytest.raises(HTTPException) as exc:
        await health(_request(scopes=["homelab:read"]), record_events=True)
    assert exc.value.status_code == 403

    # Success with scope
    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    
    events = mock_event_store.get_events()
    assert len(events) == 1
    assert events[0]["service"] == "pihole"
    assert events[0]["status"] == "new"
    assert events[0]["dedupe_key"] == "homelab:pihole:container:exited"

@pytest.mark.asyncio
async def test_repeated_failures_update_same_event_count(mock_event_store, monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")

    def mock_load_services():
        return [{"name": "pihole"}]
        
    async def mock_check_service_health(srv):
        return {"name": "pihole", "status": "error", "http_status": 500}

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    
    events = mock_event_store.get_events()
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert len(events[0]["timeline"]) == 2

@pytest.mark.asyncio
async def test_resolved_events_are_not_updated(mock_event_store, monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")

    def mock_load_services():
        return [{"name": "pihole"}]
        
    async def mock_check_service_health(srv):
        return {"name": "pihole", "status": "error", "http_status": 500}

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    # First event
    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    events = mock_event_store.get_events()
    assert len(events) == 1
    
    # Resolve the event
    mock_event_store.update_status(events[0]["id"], "resolved", "alice")
    
    # Second failure creates new event instead of updating the resolved one
    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    events = mock_event_store.get_events()
    assert len(events) == 2

@pytest.mark.asyncio
async def test_events_routes_scopes(mock_event_store):
    router = setup_event_routes()
    
    list_events = _endpoint(router, "/api/events", "GET")
    with pytest.raises(HTTPException):
        await list_events(_request(scopes=["chat"]))
        
    mock_event_store.record_event("test", "test", "error", "title", "summary", "test:key")
    events = mock_event_store.get_events()
    event_id = events[0]["id"]

    ack_event = _endpoint(router, "/api/events/{event_id}/ack", "POST")
    with pytest.raises(HTTPException):
        await ack_event(_request(scopes=["events:read"]), event_id=event_id)

    res = await ack_event(_request(scopes=["events:ack"]), event_id=event_id)
    assert res["status"] == "ok"
    assert res["event"]["status"] == "acknowledged"

@pytest.mark.asyncio
async def test_event_state_transitions(mock_event_store):
    router = setup_event_routes()
    resolve_event = _endpoint(router, "/api/events/{event_id}/resolve", "POST")
    ignore_event = _endpoint(router, "/api/events/{event_id}/ignore", "POST")

    e1 = mock_event_store.record_event("test", "test1", "error", "title", "summary", "key1")
    e2 = mock_event_store.record_event("test", "test2", "error", "title", "summary", "key2")

    await resolve_event(_request(scopes=["events:resolve"]), event_id=e1["id"])
    assert mock_event_store.get_event(e1["id"])["status"] == "resolved"

    await ignore_event(_request(scopes=["events:resolve"]), event_id=e2["id"])
    assert mock_event_store.get_event(e2["id"])["status"] == "ignored"
