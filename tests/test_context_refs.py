"""Tests for library context refs (src/context_refs.py)."""

import asyncio
import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from src import context_refs
from core.database import SessionLocal, Session as DbSession, Document, ChatMessage as DBChatMessage


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _make_doc(id="doc-1", title="My Doc", content="hello world", owner="alice", session_id=None):
    doc = MagicMock()
    doc.id = id
    doc.title = title
    doc.current_content = content
    doc.owner = owner
    doc.session_id = session_id
    return doc


def _make_session(id="sess-1", name="Chat", owner="alice"):
    sess = MagicMock()
    sess.id = id
    sess.name = name
    sess.owner = owner
    return sess


def _make_db_row(owner="alice"):
    row = MagicMock()
    row.owner = owner
    return row


# ── resolve_ref: document ─────────────────────────────────────────────────── #

def test_resolve_document_ok(monkeypatch):
    doc = _make_doc()
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = doc
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    result = context_refs.resolve_ref({"type": "document", "id": "doc-1", "title": "My Doc"}, "alice")
    assert "library document" in result["label"]
    assert result["content"] == "hello world"


def test_resolve_document_wrong_owner(monkeypatch):
    doc = _make_doc(owner="alice")
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = doc
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    with pytest.raises(HTTPException) as exc:
        context_refs.resolve_ref({"type": "document", "id": "doc-1", "title": "My Doc"}, "bob")
    assert exc.value.status_code == 404


def test_resolve_document_legacy_session_owner(monkeypatch):
    # Document with no explicit owner: ownership derived from linked session.
    doc = _make_doc(owner=None, session_id="sess-1")

    def _fake_query(model):
        q = MagicMock()
        # Document lookup
        if getattr(model, "__name__", None) == "Document":
            q.filter.return_value.first.return_value = doc
        # Session/owner lookup (model may be DbSession or a column like DbSession.owner)
        elif getattr(model, "__name__", None) == "Session" or getattr(getattr(model, "class_", None), "__name__", None) == "Session":
            q.filter.return_value.first.return_value = _make_db_row("alice")
        return q

    fake_db = MagicMock()
    fake_db.query.side_effect = _fake_query
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    result = context_refs.resolve_ref({"type": "document", "id": "doc-1", "title": "My Doc"}, "alice")
    assert result["content"] == "hello world"


# ── resolve_ref: research ─────────────────────────────────────────────────── #

def test_resolve_research_ok(tmp_path, monkeypatch):
    data = {
        "owner": "alice",
        "query": "Q",
        "result": "report text",
        "sources": ["s1", "s2"],
    }
    research_path = tmp_path / "research-1.json"
    research_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(context_refs, "DEEP_RESEARCH_DIR", str(tmp_path))

    result = context_refs.resolve_ref({"type": "research", "id": "research-1", "title": "Q"}, "alice")
    assert "library research" in result["label"]
    assert "report text" in result["content"]
    assert "s1" in result["content"]


def test_resolve_research_wrong_owner(tmp_path, monkeypatch):
    data = {"owner": "alice", "query": "Q", "result": "report text"}
    research_path = tmp_path / "research-1.json"
    research_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(context_refs, "DEEP_RESEARCH_DIR", str(tmp_path))

    with pytest.raises(HTTPException) as exc:
        context_refs.resolve_ref({"type": "research", "id": "research-1", "title": "Q"}, "bob")
    assert exc.value.status_code == 404


# ── resolve_ref: session ──────────────────────────────────────────────────── #

def test_resolve_session_ok(monkeypatch):
    sess = _make_session(id="sess-1", name="My Chat", owner="alice")

    def _fake_query(model):
        q = MagicMock()
        if model is DbSession:
            q.filter.return_value.first.return_value = sess
        elif model is DBChatMessage:
            q.filter.return_value.order_by.return_value.all.return_value = [
                MagicMock(role="user", content="hi"),
                MagicMock(role="assistant", content="hello"),
            ]
        return q

    fake_db = MagicMock()
    fake_db.query.side_effect = _fake_query
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    result = context_refs.resolve_ref({"type": "session", "id": "sess-1", "title": "My Chat"}, "alice")
    assert "library chat transcript" in result["label"]
    assert "User: hi" in result["content"]
    assert "Assistant: hello" in result["content"]


def test_resolve_session_skips_tools_and_empty(monkeypatch):
    sess = _make_session(id="sess-1", name="My Chat", owner="alice")

    def _fake_query(model):
        q = MagicMock()
        if model is DbSession:
            q.filter.return_value.first.return_value = sess
        elif model is DBChatMessage:
            q.filter.return_value.order_by.return_value.all.return_value = [
                MagicMock(role="tool", content="tool output"),
                MagicMock(role="user", content=""),
                MagicMock(role="assistant", content="ok"),
            ]
        return q

    fake_db = MagicMock()
    fake_db.query.side_effect = _fake_query
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    result = context_refs.resolve_ref({"type": "session", "id": "sess-1", "title": "My Chat"}, "alice")
    assert "tool output" not in result["content"]
    assert "ok" in result["content"]


def test_resolve_session_wrong_owner(monkeypatch):
    sess = _make_session(id="sess-1", name="My Chat", owner="alice")
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = sess
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    with pytest.raises(HTTPException) as exc:
        context_refs.resolve_ref({"type": "session", "id": "sess-1", "title": "My Chat"}, "bob")
    assert exc.value.status_code == 404


# ── truncation ────────────────────────────────────────────────────────────── #

def test_resolve_document_truncates(monkeypatch):
    long_content = "x" * (context_refs.MAX_REF_CHARS + 100)
    doc = _make_doc(content=long_content)
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = doc
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    result = context_refs.resolve_ref({"type": "document", "id": "doc-1", "title": "My Doc"}, "alice")
    assert result["content"].endswith(context_refs.TRUNCATED_SUFFIX)
    assert len(result["content"]) <= context_refs.MAX_REF_CHARS + len(context_refs.TRUNCATED_SUFFIX)


# ── build_context_messages ────────────────────────────────────────────────── #

def test_build_context_messages_dedupes_and_skips_invalid(monkeypatch):
    doc = _make_doc()
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = doc
    monkeypatch.setattr(context_refs, "SessionLocal", lambda: fake_db)

    refs = [
        {"type": "document", "id": "doc-1", "title": "My Doc"},
        {"type": "document", "id": "doc-1", "title": "My Doc"},  # dup
        {"type": "invalid", "id": "x", "title": "Bad"},
    ]
    messages = context_refs.build_context_messages(refs, "alice")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "library document" in messages[0]["content"]


# ── validate_refs ─────────────────────────────────────────────────────────── #

def test_validate_refs_enforces_max():
    refs = [{"type": "document", "id": f"doc-{i}", "title": f"D{i}"} for i in range(context_refs.MAX_REFS_PER_MESSAGE + 1)]
    with pytest.raises(HTTPException) as exc:
        context_refs.validate_refs(refs)
    assert exc.value.status_code == 400


def test_validate_refs_parses_json_string():
    refs = context_refs.validate_refs('[{"type":"document","id":"doc-1","title":"T"}]')
    assert refs[0]["type"] == "document"


# ── preflight endpoint ────────────────────────────────────────────────────── #

def test_preflight_accepts_within_budget(monkeypatch):
    from routes.context_refs_routes import setup_context_refs_routes, PreflightRequest, PreflightCandidate

    fake_session = MagicMock()
    fake_session.endpoint_url = "http://test/v1"
    fake_session.model = "test-model"
    fake_session.get_context_messages.return_value = []

    sm = MagicMock()
    sm.get_session.return_value = fake_session
    router = setup_context_refs_routes(sm)
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/context_refs/preflight")

    monkeypatch.setattr("routes.context_refs_routes.get_context_length", lambda _e, _m: 10000)
    monkeypatch.setattr("routes.context_refs_routes.estimate_tokens", lambda _m: 100)
    monkeypatch.setattr("routes.context_refs_routes.estimate_ref_tokens", lambda _r, _o: 50)
    monkeypatch.setattr("routes.context_refs_routes.load_settings", lambda: {"agent_input_token_budget": 6000, "agent_input_token_hard_max": 200_000})
    monkeypatch.setattr("routes.context_refs_routes.is_setting_overridden", lambda _k: False)
    monkeypatch.setattr("routes.context_refs_routes._verify_session_owner", lambda _r, _s, session_manager=None: None)

    class FakeRequest:
        app = MagicMock()
        state = MagicMock(current_user="alice")

    req = FakeRequest()
    result = asyncio.run(endpoint(
        body=PreflightRequest(session_id="sess-1", refs=[], candidate=PreflightCandidate(type="document", id="doc-1", title="Doc")),
        request=req,
    ))
    assert result["ok"] is True
    assert result["budget"] > 0


def test_preflight_rejects_over_budget(monkeypatch):
    from routes.context_refs_routes import setup_context_refs_routes, PreflightRequest, PreflightCandidate

    fake_session = MagicMock()
    fake_session.endpoint_url = "http://test/v1"
    fake_session.model = "test-model"
    fake_session.get_context_messages.return_value = []

    sm = MagicMock()
    sm.get_session.return_value = fake_session
    router = setup_context_refs_routes(sm)
    endpoint = next(r.endpoint for r in router.routes if getattr(r, "path", "") == "/api/context_refs/preflight")

    monkeypatch.setattr("routes.context_refs_routes.get_context_length", lambda _e, _m: 10000)
    monkeypatch.setattr("routes.context_refs_routes.estimate_tokens", lambda _m: 5000)
    monkeypatch.setattr("routes.context_refs_routes.estimate_ref_tokens", lambda _r, _o: 5000)
    monkeypatch.setattr("routes.context_refs_routes.load_settings", lambda: {"agent_input_token_budget": 6000, "agent_input_token_hard_max": 200_000})
    monkeypatch.setattr("routes.context_refs_routes.is_setting_overridden", lambda _k: False)
    monkeypatch.setattr("routes.context_refs_routes._verify_session_owner", lambda _r, _s, session_manager=None: None)

    class FakeRequest:
        app = MagicMock()
        state = MagicMock(current_user="alice")

    req = FakeRequest()
    result = asyncio.run(endpoint(
        body=PreflightRequest(session_id="sess-1", refs=[], candidate=PreflightCandidate(type="document", id="doc-1", title="Doc")),
        request=req,
    ))
    assert result["ok"] is False
    assert "Remove a context chip" in result["message"]
