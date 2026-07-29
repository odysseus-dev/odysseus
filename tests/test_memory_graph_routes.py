"""Memory Graph View route tests.

Follows the repo's established convention (see
tests/test_memory_routes_session_owner.py): build the router via its
setup_*_routes factory directly, monkeypatch auth helpers, look up the
target endpoint by path, and call it directly with a hand-built Request
stand-in. No TestClient/ASGI app — except in
test_memory_graph_route_ordering.py, which specifically needs real Starlette
path matching.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import routes.memory.memory_graph_routes as mgr


def _route(router, path, method):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(path)


def _request(user):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _allow_memory_management(monkeypatch, caller):
    monkeypatch.setattr(mgr, "require_privilege", lambda request, key: caller)


def test_graph_only_returns_callers_own_memories(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    monkeypatch.setattr(mgr, "require_user", lambda request: "alice", raising=False)
    memory_manager = MagicMock()
    memory_manager.load.side_effect = lambda owner=None: (
        [{"id": "m1", "text": "alice's note", "owner": "alice", "category": "fact", "uses": 0, "timestamp": 1}]
        if owner == "alice" else []
    )
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    get_graph = _route(router, "/api/memory/graph", "GET")

    out = get_graph(request=_request("alice"), category=None, min_similarity=0.75,
                     max_edges_per_node=5, include_session_edges=True,
                     include_manual_edges=True, limit=1000)

    assert [n["id"] for n in out["nodes"]] == ["m1"]
    memory_manager.load.assert_called_with(owner="alice")


def test_graph_neighbors_rejects_foreign_owned_memory(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "bob", raising=False)
    monkeypatch.setattr(mgr, "require_user", lambda request: "bob", raising=False)
    memory_manager = MagicMock()
    memory_manager.load.return_value = [
        {"id": "victim-mem", "text": "alice secret", "owner": "alice", "category": "fact", "uses": 0, "timestamp": 1},
    ]
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    neighbors = _route(router, "/api/memory/graph/{memory_id}/neighbors", "GET")

    with pytest.raises(HTTPException) as exc:
        neighbors(request=_request("bob"), memory_id="victim-mem", min_similarity=0.75, max_edges_per_node=5)
    assert exc.value.status_code == 404


def test_graph_neighbors_returns_404_for_unknown_id(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    monkeypatch.setattr(mgr, "require_user", lambda request: "alice", raising=False)
    memory_manager = MagicMock()
    memory_manager.load.return_value = []
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    neighbors = _route(router, "/api/memory/graph/{memory_id}/neighbors", "GET")

    with pytest.raises(HTTPException) as exc:
        neighbors(request=_request("alice"), memory_id="nope", min_similarity=0.75, max_edges_per_node=5)
    assert exc.value.status_code == 404


def test_graph_neighbors_scopes_to_connected_subgraph(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    monkeypatch.setattr(mgr, "require_user", lambda request: "alice", raising=False)
    memory_manager = MagicMock()
    memory_manager.load.return_value = [
        {"id": "a", "text": "t", "owner": "alice", "category": "fact", "uses": 0, "timestamp": 1, "session_id": "s1"},
        {"id": "b", "text": "t", "owner": "alice", "category": "fact", "uses": 0, "timestamp": 1, "session_id": "s1"},
        {"id": "c", "text": "t", "owner": "alice", "category": "fact", "uses": 0, "timestamp": 1},
    ]
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    neighbors = _route(router, "/api/memory/graph/{memory_id}/neighbors", "GET")

    out = neighbors(request=_request("alice"), memory_id="a", min_similarity=0.75, max_edges_per_node=5)

    assert {n["id"] for n in out["nodes"]} == {"a", "b"}


def test_add_link_requires_can_manage_memory_privilege(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)

    def deny(request, key):
        raise HTTPException(403, "nope")
    monkeypatch.setattr(mgr, "require_privilege", deny)

    memory_manager = MagicMock()
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    add_link = _route(router, "/api/memory/{memory_id}/links", "POST")

    with pytest.raises(HTTPException) as exc:
        add_link(request=_request("alice"), memory_id="a", target_id="b")
    assert exc.value.status_code == 403
    memory_manager.save.assert_not_called()


def test_add_link_rejects_foreign_owned_target(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "bob", raising=False)
    _allow_memory_management(monkeypatch, "bob")
    memory_manager = MagicMock()
    memory_manager.load_all.return_value = [
        {"id": "bob-mem", "text": "t", "owner": "bob"},
        {"id": "alice-mem", "text": "t", "owner": "alice"},
    ]
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    add_link = _route(router, "/api/memory/{memory_id}/links", "POST")

    with pytest.raises(HTTPException) as exc:
        add_link(request=_request("bob"), memory_id="bob-mem", target_id="alice-mem")
    assert exc.value.status_code == 404
    memory_manager.save.assert_not_called()


def test_add_link_rejects_self_link(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    _allow_memory_management(monkeypatch, "alice")
    memory_manager = MagicMock()
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    add_link = _route(router, "/api/memory/{memory_id}/links", "POST")

    with pytest.raises(HTTPException) as exc:
        add_link(request=_request("alice"), memory_id="a", target_id="a")
    assert exc.value.status_code == 400
    memory_manager.save.assert_not_called()


def test_add_link_persists_bidirectionally_addressable_link(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    _allow_memory_management(monkeypatch, "alice")
    memory_manager = MagicMock()
    entries = [
        {"id": "a", "text": "t", "owner": "alice"},
        {"id": "b", "text": "t", "owner": "alice"},
    ]
    memory_manager.load_all.return_value = entries
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    add_link = _route(router, "/api/memory/{memory_id}/links", "POST")

    out = add_link(request=_request("alice"), memory_id="a", target_id="b")

    assert out == {"ok": True, "links": ["b"]}
    memory_manager.save.assert_called_once_with(entries)
    assert entries[0]["links"] == ["b"]


def test_add_link_is_idempotent_when_link_already_present(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    _allow_memory_management(monkeypatch, "alice")
    memory_manager = MagicMock()
    entries = [
        {"id": "a", "text": "t", "owner": "alice", "links": ["b"]},
        {"id": "b", "text": "t", "owner": "alice"},
    ]
    memory_manager.load_all.return_value = entries
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    add_link = _route(router, "/api/memory/{memory_id}/links", "POST")

    out = add_link(request=_request("alice"), memory_id="a", target_id="b")

    assert out["links"] == ["b"]


def test_remove_link_is_idempotent_when_link_absent(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "alice", raising=False)
    _allow_memory_management(monkeypatch, "alice")
    memory_manager = MagicMock()
    memory_manager.load_all.return_value = [{"id": "a", "text": "t", "owner": "alice"}]
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    remove_link = _route(router, "/api/memory/{memory_id}/links/{target_id}", "DELETE")

    out = remove_link(request=_request("alice"), memory_id="a", target_id="never-linked")

    assert out == {"ok": True, "links": []}
    memory_manager.save.assert_not_called()


def test_remove_link_rejects_foreign_owned_source(monkeypatch):
    monkeypatch.setattr(mgr, "get_current_user", lambda request: "bob", raising=False)
    _allow_memory_management(monkeypatch, "bob")
    memory_manager = MagicMock()
    memory_manager.load_all.return_value = [{"id": "alice-mem", "text": "t", "owner": "alice", "links": ["x"]}]
    router = mgr.setup_memory_graph_routes(memory_manager, memory_vector=None)
    remove_link = _route(router, "/api/memory/{memory_id}/links/{target_id}", "DELETE")

    with pytest.raises(HTTPException) as exc:
        remove_link(request=_request("bob"), memory_id="alice-mem", target_id="x")
    assert exc.value.status_code == 404
