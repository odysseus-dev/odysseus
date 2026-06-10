from datetime import datetime, timezone
import os
import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Avoid importing core/__init__.py in this lightweight test environment; it
# pulls in SQLAlchemy models before SQLAlchemy is installed.
if "core" not in sys.modules:
    core_pkg = types.ModuleType("core")
    core_pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(__file__)), "core")]
    sys.modules["core"] = core_pkg

from core.middleware import require_admin
from routes.unified_search_routes import setup_unified_search_routes


def _app_with_searchers(searchers, *, allow_admin=True):
    app = FastAPI()
    app.include_router(setup_unified_search_routes(searchers=searchers))
    if allow_admin:
        app.dependency_overrides[require_admin] = lambda: None
    return app


def test_unified_search_federates_ranked_results_and_degrades_failed_surface():
    def chat_search(query, limit, owner, request):
        return [{
            "type": "chat",
            "id": "msg-1",
            "title": "Planning chat",
            "snippet": "We discussed the Apollo launch checklist.",
            "source_ref": {"session_id": "sess-1", "message_id": "msg-1"},
            "timestamp": "2026-05-30T12:00:00Z",
            "score": 0.1,
        }]

    def document_search(query, limit, owner, request):
        return [{
            "type": "document",
            "id": "doc-1",
            "title": "Apollo checklist",
            "snippet": "Launch notes and owner handoff.",
            "source_ref": {"document_id": "doc-1"},
            "timestamp": datetime(2026, 5, 31, tzinfo=timezone.utc),
            "score": 0.3,
        }]

    def broken_email_search(query, limit, owner, request):
        raise RuntimeError("imap not configured")

    app = _app_with_searchers({
        "chat": chat_search,
        "document": document_search,
        "email": broken_email_search,
    })
    res = TestClient(app).get("/api/search/all?q=apollo&limit=10")

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert [r["type"] for r in data["results"]] == ["document", "chat"]
    assert set(data["grouped"]) == {"document", "chat"}
    assert "email" not in data["grouped"]


def test_unified_search_respects_type_filter():
    calls = {"chat": 0, "document": 0}

    def chat_search(query, limit, owner, request):
        calls["chat"] += 1
        return [{
            "type": "chat",
            "id": "msg-1",
            "title": "Needle",
            "snippet": query,
            "source_ref": {"session_id": "sess-1"},
        }]

    def document_search(query, limit, owner, request):
        calls["document"] += 1
        return [{
            "type": "document",
            "id": "doc-1",
            "title": "Needle",
            "snippet": query,
            "source_ref": {"document_id": "doc-1"},
        }]

    app = _app_with_searchers({"chat": chat_search, "document": document_search})
    res = TestClient(app).get("/api/search/all?q=needle&types=chats&limit=10")

    assert res.status_code == 200
    assert [r["type"] for r in res.json()["results"]] == ["chat"]
    assert calls == {"chat": 1, "document": 0}


def test_unified_search_is_admin_gated():
    app = _app_with_searchers({"chat": lambda *args: []}, allow_admin=False)
    res = TestClient(app).get("/api/search/all?q=anything")

    assert res.status_code == 403
