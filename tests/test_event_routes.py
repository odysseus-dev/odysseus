import json
import os
from types import SimpleNamespace
from unittest.mock import patch, mock_open

import pytest
from fastapi import HTTPException, APIRouter

from src.event_store import EventStore
from routes.event_routes import setup_event_routes
from routes.homelab_routes import setup_homelab_routes

def _request(*, api_token=True, scopes=None, owner="alice", current_user="browser-user", **kwargs):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    req = SimpleNamespace(state=state, app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))
    for k, v in kwargs.items():
        setattr(req, k, v)
    return req

def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")

@pytest.fixture
def mock_event_store(monkeypatch, tmp_path):
    store_file = str(tmp_path / "data" / "homelab_events.json")
    os.makedirs(os.path.dirname(store_file), exist_ok=True)
    monkeypatch.setattr("src.event_store.EVENTS_FILE", store_file)
    monkeypatch.setattr("routes.event_routes.EventStore", lambda: EventStore(store_file))
    monkeypatch.setattr("routes.homelab_routes.EventStore", lambda: EventStore(store_file))
    return EventStore(store_file)

def test_router_duplication():
    # Calling it twice should not append duplicate routes
    r1 = setup_event_routes()
    r2 = setup_event_routes()
    assert len(r1.routes) == len(r2.routes)
    paths = [getattr(r, "path", "") for r in r1.routes]
    assert len(paths) == len(set(paths))

def test_atomic_write(mock_event_store, monkeypatch):
    calls = []
    original_replace = os.replace
    def mock_replace(src, dst):
        calls.append((src, dst))
        original_replace(src, dst)
        
    monkeypatch.setattr("os.replace", mock_replace)
    mock_event_store.record_event("test", "test", "warning", "title", "sum", "key")
    
    assert len(calls) == 1
    assert calls[0][0].endswith(".tmp")
    assert calls[0][1].endswith("homelab_events.json")

def test_save_failure_raises(mock_event_store, monkeypatch):
    def mock_replace(*args, **kwargs):
        raise OSError("Permission denied")
    monkeypatch.setattr("os.replace", mock_replace)
    
    with pytest.raises(IOError) as exc:
        mock_event_store.record_event("test", "test", "warning", "t", "s", "key")
    assert "Persistence failure" in str(exc.value)

@pytest.mark.asyncio
async def test_severity_mapping(mock_event_store, monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")

    def mock_load_services():
        return [{"name": "s1"}, {"name": "s2"}]
        
    async def mock_check_service_health(srv, *, client=None):
        if srv["name"] == "s1":
            return {"name": "s1", "status": "error", "container_status": "exited"}
        return {"name": "s2", "status": "degraded", "http_status": 502}

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    events = mock_event_store.get_events()
    
    s1_event = next(e for e in events if e["service"] == "s1")
    s2_event = next(e for e in events if e["service"] == "s2")
    
    assert s1_event["severity"] == "critical"
    assert s2_event["severity"] == "warning"

@pytest.mark.asyncio
async def test_stable_dedupe(mock_event_store, monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")

    def mock_load_services():
        return [{"name": "pihole"}]
        
    status_to_return = {"name": "pihole", "status": "error", "http_status": 500}
    async def mock_check_service_health(srv, *, client=None):
        return status_to_return

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    
    # Change HTTP status to 502, it should still dedupe
    status_to_return = {"name": "pihole", "status": "error", "http_status": 502}
    await health(_request(scopes=["homelab:read", "events:write"]), record_events=True)
    
    events = mock_event_store.get_events()
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert events[0]["dedupe_key"] == "homelab:pihole:health"

@pytest.mark.asyncio
async def test_investigate_route(mock_event_store):
    router = setup_event_routes()
    investigate = _endpoint(router, "/api/events/{event_id}/investigate", "POST")

    e = mock_event_store.record_event("src", "srv", "critical", "t", "s", "k")
    await investigate(_request(scopes=["events:ack"]), event_id=e["id"])
    
    event = mock_event_store.get_event(e["id"])
    assert event["status"] == "investigating"

@pytest.mark.asyncio
async def test_event_summary_filters(mock_event_store):
    router = setup_event_routes()
    list_events = _endpoint(router, "/api/events", "GET")
    
    mock_event_store.record_event("src", "s1", "critical", "t", "s", "k1")
    e2 = mock_event_store.record_event("src", "s2", "warning", "t", "s", "k2")
    mock_event_store.update_status(e2["id"], "resolved")
    
    # All
    res = await list_events(_request(scopes=["events:read"]), status=None, limit=None)
    assert len(res["events"]) == 2
    
    # Open
    res = await list_events(_request(scopes=["events:read"]), status="open", limit=None)
    assert len(res["events"]) == 1
    assert res["events"][0]["status"] == "new"

    # Resolved
    res = await list_events(_request(scopes=["events:read"]), status="resolved", limit=None)
    assert len(res["events"]) == 1
    assert res["events"][0]["status"] == "resolved"
    
    # Limit
    mock_event_store.update_status(e2["id"], "new")
    res = await list_events(_request(scopes=["events:read"]), status="open", limit=1)
    assert len(res["events"]) == 1

@pytest.mark.asyncio
async def test_suggested_actions(mock_event_store):
    router = setup_event_routes()
    list_events = _endpoint(router, "/api/events", "GET")
    
    mock_event_store.record_event("src", "s1", "critical", "t", "s", "k1")
    res = await list_events(_request(scopes=["events:read"]), status=None, limit=None)
    
    event = res["events"][0]
    actions = event["suggested_actions"]
    assert "ack" in actions
    assert "investigate" in actions
    assert "resolve" in actions
    assert "ignore" in actions
    assert "view_service" in actions
    assert "restart" not in actions

@pytest.mark.asyncio
async def test_route_save_failure_500(mock_event_store, monkeypatch):
    router = setup_event_routes()
    ack = _endpoint(router, "/api/events/{event_id}/ack", "POST")
    investigate = _endpoint(router, "/api/events/{event_id}/investigate", "POST")
    resolve = _endpoint(router, "/api/events/{event_id}/resolve", "POST")
    ignore = _endpoint(router, "/api/events/{event_id}/ignore", "POST")
    
    e = mock_event_store.record_event("src", "s1", "critical", "t", "s", "k1")
    
    def mock_save(*args):
        raise IOError("fail")
    monkeypatch.setattr("src.event_store.EventStore._save", mock_save)
    
    for ep, scope in [(ack, "events:ack"), (investigate, "events:ack"),
                      (resolve, "events:resolve"), (ignore, "events:resolve")]:
        with pytest.raises(HTTPException) as exc:
            await ep(_request(scopes=[scope]), event_id=e["id"])
        assert exc.value.status_code == 500

@pytest.mark.asyncio
async def test_missing_event_returns_404(mock_event_store):
    router = setup_event_routes()
    ack = _endpoint(router, "/api/events/{event_id}/ack", "POST")
    investigate = _endpoint(router, "/api/events/{event_id}/investigate", "POST")
    resolve = _endpoint(router, "/api/events/{event_id}/resolve", "POST")
    ignore = _endpoint(router, "/api/events/{event_id}/ignore", "POST")

    fake_id = "non-existent-id"

    for ep, scope in [(ack, "events:ack"), (investigate, "events:ack"), 
                      (resolve, "events:resolve"), (ignore, "events:resolve")]:
        with pytest.raises(HTTPException) as exc:
            await ep(_request(scopes=[scope]), event_id=fake_id)
        assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_event_sorting(mock_event_store):
    router = setup_event_routes()
    summary = _endpoint(router, "/api/events/summary", "GET")

    e1 = mock_event_store.record_event("src", "s1", "warning", "t1", "s1", "k1")
    e2 = mock_event_store.record_event("src", "s2", "critical", "t2", "s2", "k2")
    
    # Manipulate last_seen to make e1 newer than e2
    events = mock_event_store._load()
    for e in events:
        if e["id"] == e1["id"]:
            e["last_seen"] = "2099-01-01T00:00:00+00:00"
        elif e["id"] == e2["id"]:
            e["last_seen"] = "2020-01-01T00:00:00+00:00"
    mock_event_store._save(events)

    res = await summary(_request(scopes=["events:read"]))
    assert len(res["events"]) == 2
    assert res["events"][0]["id"] == e1["id"]
    assert res["events"][1]["id"] == e2["id"]
