"""Memory routes must owner-scope caller-supplied session ids.

SessionManager.get_session returns any session by id (no owner scoping). The
/api/memory extract, audit, import, and by-session handlers accept a
caller-supplied session id, so without an ownership gate a user could target
another tenant's session and leak their chat history, session-scoped LLM
credentials, or session title.
"""
import asyncio
import io
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

import routes.memory_routes as mr
from src.request_models import MemoryAddRequest


def _route(router, path, method):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(path)


def _router(monkeypatch, caller):
    monkeypatch.setattr(mr, "get_current_user", lambda request: caller, raising=False)
    monkeypatch.setattr(mr, "require_user", lambda request: caller, raising=False)
    sm = MagicMock()
    sm.sessions = {}
    sm.get_session = lambda sid: SimpleNamespace(
        owner="alice", name="Secret project", endpoint_url="http://x", model="m",
        headers={"Authorization": "Bearer victim-secret"},
        get_context_messages=lambda: [],
    )
    mem = MagicMock()
    mem.load = lambda owner=None: []
    return mr.setup_memory_routes(mem, sm)


def _request(user):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )


def _upload(name="memories.json"):
    return UploadFile(
        filename=name,
        file=io.BytesIO(b'[{"text": "Project Phoenix uses Python", "category": "project"}]'),
    )


def _allow_memory_management(monkeypatch):
    monkeypatch.setattr("src.auth_helpers.require_privilege", lambda request, privilege: "alice")


def test_extract_rejects_other_users_session(monkeypatch):
    router = _router(monkeypatch, caller="bob")
    extract = _route(router, "/api/memory/extract", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(extract(request=None, session="alice-sess"))
    assert exc.value.status_code == 404


def test_by_session_rejects_other_users_session(monkeypatch):
    router = _router(monkeypatch, caller="bob")
    gbs = _route(router, "/api/memory/by-session/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        gbs(request=None, session_id="alice-sess")
    assert exc.value.status_code == 404


def test_owner_can_access_own_session(monkeypatch):
    router = _router(monkeypatch, caller="alice")
    gbs = _route(router, "/api/memory/by-session/{session_id}", "GET")
    out = gbs(request=None, session_id="alice-sess")
    assert out["session_name"] == "Secret project"


def test_audit_session_fallback_uses_resolver_without_manual_default(monkeypatch):
    import src.task_endpoint as task_endpoint

    memory_manager = MagicMock()
    memory_vector = MagicMock()
    session_headers = {"Authorization": "Bearer session"}
    session_manager = MagicMock()
    session_manager.get_session.return_value = SimpleNamespace(
        owner="alice",
        endpoint_url="http://session.example/v1/chat/completions",
        model="session-model",
        headers=session_headers,
    )
    router = mr.setup_memory_routes(memory_manager, session_manager, memory_vector)
    audit_route = _route(router, "/api/memory/audit", "POST")

    resolver_calls = []
    audit_calls = []

    def fake_resolve_task_endpoint(
        fallback_url=None,
        fallback_model=None,
        fallback_headers=None,
        owner=None,
    ):
        resolver_calls.append((fallback_url, fallback_model, fallback_headers, owner))
        if fallback_url and fallback_model:
            return fallback_url, fallback_model, fallback_headers
        return None, None, {}

    async def fake_audit_memories(memory_manager_arg, memory_vector_arg, endpoint_url, model, headers, owner=None):
        audit_calls.append((memory_manager_arg, memory_vector_arg, endpoint_url, model, headers, owner))
        return {"before": 2, "after": 1}

    fake_model_routes = types.ModuleType("routes.model_routes")
    fake_model_routes._load_settings = lambda: {
        "default_endpoint_id": "default",
        "default_model": "default-model",
    }
    fake_model_routes._normalize_base = lambda base: base.rstrip("/")
    fake_model_routes.build_chat_url = lambda base: f"{base}/chat/completions"

    monkeypatch.setattr(mr, "resolve_task_endpoint", fake_resolve_task_endpoint)
    monkeypatch.setattr(task_endpoint, "resolve_task_endpoint", fake_resolve_task_endpoint)
    monkeypatch.setattr(mr, "audit_memories", fake_audit_memories)
    monkeypatch.setitem(sys.modules, "routes.model_routes", fake_model_routes)
    monkeypatch.setattr(
        mr,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("manual default branch should not run")),
    )

    out = asyncio.run(audit_route(request=_request("alice"), session="session-1"))

    assert resolver_calls == [(
        "http://session.example/v1/chat/completions",
        "session-model",
        session_headers,
        "alice",
    )]
    assert audit_calls == [(
        memory_manager,
        memory_vector,
        "http://session.example/v1/chat/completions",
        "session-model",
        session_headers,
        "alice",
    )]
    assert out["ok"] is True
    assert out["removed"] == 1


def test_add_memory_rejects_other_users_session(monkeypatch):
    memory_manager = MagicMock()
    session_manager = MagicMock()
    memory_vector = MagicMock(healthy=True)
    router = mr.setup_memory_routes(
        memory_manager=memory_manager,
        session_manager=session_manager,
        memory_vector=memory_vector,
    )
    add_memory = _route(router, "/api/memory/add", "POST")

    memory_manager.load.return_value = []
    memory_manager.find_duplicates.return_value = False
    session_manager.get_session.return_value = SimpleNamespace(owner="bob", name="Bob session")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            add_memory(
                request=_request("alice"),
                memory_data=MemoryAddRequest(
                    text="Alice note",
                    category="fact",
                    source="user",
                    session_id="bob-session",
                ),
            )
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Session not found"
    session_manager.get_session.assert_called_once_with("bob-session")
    memory_manager.add_entry.assert_not_called()
    memory_manager.save.assert_not_called()
    memory_vector.add.assert_not_called()


def test_timeline_does_not_expose_other_users_session_name():
    memory_manager = MagicMock()
    session_manager = MagicMock()
    session_manager.sessions = {"bob-session": object()}
    session_manager.get_session.return_value = SimpleNamespace(owner="bob", name="Bob roadmap")
    memory_manager.load.return_value = [
        {
            "id": "m1",
            "text": "Alice note",
            "owner": "alice",
            "session_id": "bob-session",
            "timestamp": 1,
        }
    ]
    router = mr.setup_memory_routes(memory_manager, session_manager)
    timeline = _route(router, "/api/memory/timeline", "GET")

    out = timeline(request=_request("alice"))

    assert out["timeline"][0]["session_name"] == "Unknown"


def test_import_missing_session_uses_utility_fallback(monkeypatch):
    _allow_memory_management(monkeypatch)
    memory_manager = MagicMock()
    session_manager = MagicMock()
    session_manager.get_session.side_effect = KeyError
    resolve_endpoint = MagicMock(return_value=("http://utility", "utility-model", {}))
    resolve_task_endpoint = MagicMock(side_effect=AssertionError("session task endpoint should not be used"))
    monkeypatch.setattr(mr, "resolve_endpoint", resolve_endpoint)
    monkeypatch.setattr(mr, "resolve_task_endpoint", resolve_task_endpoint)
    router = mr.setup_memory_routes(memory_manager, session_manager)
    import_memories = _route(router, "/api/memory/import", "POST")

    out = asyncio.run(import_memories(request=_request("alice"), session="missing-session", file=_upload()))

    assert out == {
        "suggestions": [{"text": "Project Phoenix uses Python", "category": "project"}],
        "filename": "memories.json",
    }
    session_manager.get_session.assert_called_once_with("missing-session")
    resolve_endpoint.assert_called_once_with("utility", owner="alice")


def test_import_foreign_session_uses_same_utility_fallback(monkeypatch):
    _allow_memory_management(monkeypatch)
    memory_manager = MagicMock()
    session_manager = MagicMock()
    session_manager.get_session.return_value = SimpleNamespace(
        owner="bob",
        endpoint_url="http://bob-llm",
        model="bob-model",
        headers={"Authorization": "Bearer bob-secret"},
    )
    resolve_endpoint = MagicMock(return_value=("http://utility", "utility-model", {}))
    resolve_task_endpoint = MagicMock(side_effect=AssertionError("foreign session endpoint should not be used"))
    monkeypatch.setattr(mr, "resolve_endpoint", resolve_endpoint)
    monkeypatch.setattr(mr, "resolve_task_endpoint", resolve_task_endpoint)
    router = mr.setup_memory_routes(memory_manager, session_manager)
    import_memories = _route(router, "/api/memory/import", "POST")

    out = asyncio.run(import_memories(request=_request("alice"), session="bob-session", file=_upload()))

    assert out["suggestions"] == [{"text": "Project Phoenix uses Python", "category": "project"}]
    session_manager.get_session.assert_called_once_with("bob-session")
    resolve_endpoint.assert_called_once_with("utility", owner="alice")


def test_import_owned_session_uses_session_endpoint(monkeypatch):
    _allow_memory_management(monkeypatch)
    memory_manager = MagicMock()
    session_manager = MagicMock()
    session_manager.get_session.return_value = SimpleNamespace(
        owner="alice",
        endpoint_url="http://alice-llm",
        model="alice-model",
        headers={"X-Session": "alice"},
    )
    resolve_endpoint = MagicMock(side_effect=AssertionError("utility fallback should not be used"))
    resolve_task_endpoint = MagicMock(return_value=("http://alice-task", "alice-task-model", {"X-Task": "alice"}))
    monkeypatch.setattr(mr, "resolve_endpoint", resolve_endpoint)
    monkeypatch.setattr(mr, "resolve_task_endpoint", resolve_task_endpoint)
    router = mr.setup_memory_routes(memory_manager, session_manager)
    import_memories = _route(router, "/api/memory/import", "POST")

    out = asyncio.run(import_memories(request=_request("alice"), session="alice-session", file=_upload()))

    assert out["suggestions"] == [{"text": "Project Phoenix uses Python", "category": "project"}]
    session_manager.get_session.assert_called_once_with("alice-session")
    resolve_task_endpoint.assert_called_once_with(
        "http://alice-llm",
        "alice-model",
        {"X-Session": "alice"},
        owner="alice",
    )
    resolve_endpoint.assert_not_called()
