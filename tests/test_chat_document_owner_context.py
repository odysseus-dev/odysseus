"""The chat stream must never pass another owner's document to the model."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import AuthManager
from routes.auth_routes import SESSION_COOKIE
from tests.helpers.database import isolated_session_database


@pytest.fixture
def document_chat(tmp_path, monkeypatch):
    from routes import chat_routes, chat_helpers, session_routes
    from src.agent_tools import document_tools
    from src.auth_helpers import effective_user

    monkeypatch.setenv("AUTH_ENABLED", "true")
    auth = AuthManager(str(tmp_path / "auth.json"))
    for owner in ("alice", "bob"):
        assert auth.create_user(owner, "disposable-test-password")
    captured = []

    async def context(sess, request, *args, **kwargs):
        # Preprocessing and external model I/O are outside the owner lookup.
        # The actual route, session-owner gate and document ORM queries run.
        return chat_helpers.ChatContext(
            preface=[], rag_sources=[], web_sources=[], used_memories=[],
            messages=[{"role": "user", "content": "Summarize my writing"}],
            context_length=4096, was_compacted=False, user=effective_user(request), uprefs={},
            preset=chat_helpers.PresetInfo(0.2, 128, None, None),
            preprocessed=chat_helpers.PreprocessedMessage("", "", "", [], []),
        )

    async def model(endpoint, model, messages, **kwargs):
        doc = kwargs.get("active_document")
        captured.append(None if doc is None else (doc.id, doc.owner, doc.current_content))
        text = doc.current_content if doc is not None else "no document context"
        yield f'data: {json.dumps({"delta": text})}\n\n'
        yield 'data: [DONE]\n\n'

    monkeypatch.setattr(chat_routes, "build_chat_context", context)
    monkeypatch.setattr(chat_routes, "stream_agent_loop", model)
    monkeypatch.setattr(chat_routes, "save_assistant_response", lambda *a, **kw: None)
    monkeypatch.setattr(chat_routes, "run_post_response_tasks", lambda *a, **kw: None)
    monkeypatch.setattr(document_tools, "_active_document_id", None)
    with isolated_session_database(tmp_path) as (manager, database):
        factory = database.SessionLocal
        for module in (chat_routes, chat_helpers, session_routes):
            monkeypatch.setattr(module, "SessionLocal", factory)
        # Existing route tests may re-import session_routes after chat imported
        # its owner check. Bind that real function's database too, without
        # replacing the authorization logic or reloading production modules.
        monkeypatch.setitem(chat_routes._verify_session_owner.__globals__, "SessionLocal", factory)
        with factory() as db:
            db.add_all([
                database.Session(id="alice-chat", name="Alice", owner="alice",
                                 endpoint_url="http://model.invalid/v1", model="fixture-model"),
                database.Session(id="bob-chat", name="Bob", owner="bob",
                                 endpoint_url="http://model.invalid/v1", model="fixture-model"),
                database.ModelEndpoint(id="fixture-endpoint", name="Fixture",
                                       base_url="http://model.invalid/v1", owner="alice"),
            ])
            db.commit()
        app = FastAPI()
        app.state.auth_manager = auth

        @app.middleware("http")
        async def fixture_session(request, call_next):
            request.state.current_user = auth.get_username_for_token(request.cookies.get(SESSION_COOKIE))
            return await call_next(request)

        app.include_router(chat_routes.setup_chat_routes(
            manager, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
            SimpleNamespace(), SimpleNamespace(),
        ))
        token = auth.create_session("alice", "disposable-test-password")
        assert token
        with TestClient(app) as client:
            client.cookies.set(SESSION_COOKIE, token)
            yield SimpleNamespace(client=client, factory=factory, database=database,
                                  captured=captured, document_tools=document_tools)


@pytest.mark.parametrize("source", ["explicit", "session", "email-session", "memory"])
@pytest.mark.parametrize("document_owner", ["alice", "bob"])
def test_chat_document_context_is_owner_scoped(document_chat, source, document_owner):
    case = document_chat
    form = {"message": "Summarize my writing", "session": "alice-chat", "mode": "agent",
            "compare_mode": "true", "no_memory": "true", "use_rag": "false"}
    with case.factory() as db:
        for owner in ("alice", "bob"):
            # A stale session link is not evidence that the caller owns a doc.
            linked = "alice-chat" if source in {"session", "email-session"} and owner == document_owner else None
            db.add(case.database.Document(id=f"{owner}-doc", owner=owner, session_id=linked,
                                         title=f"{owner} draft",
                                         language="email" if source == "email-session" else "markdown",
                                         current_content=f"{owner}-private-context", is_active=True))
        db.commit()
    if source == "explicit":
        form.update(active_doc_id=f"{document_owner}-doc", active_doc_state="visible")
    elif source == "memory":
        case.document_tools.set_active_document(f"{document_owner}-doc")
    response = case.client.post("/api/chat_stream", data=form)
    assert response.status_code == 200, response.text
    if document_owner == "alice":
        assert case.captured == [("alice-doc", "alice", "alice-private-context")]
        assert "alice-private-context" in response.text
    else:
        assert case.captured == [None]
    assert "bob-private-context" not in response.text
    with case.factory() as db:
        assert db.get(case.database.Document, "bob-doc").owner == "bob"
        assert db.get(case.database.Document, "bob-doc").session_id == (
            "alice-chat" if source in {"session", "email-session"} and document_owner == "bob" else None
        )
