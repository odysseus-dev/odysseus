"""Both chat tidy entry points retain saved conversations and still remove empties."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import core.session_manager as manager_module
import routes.session_routes as routes
import src.session_actions as actions


@pytest.fixture
def tidy_api(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'tidy.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(manager_module, "SessionLocal", factory)
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(routes, "router", APIRouter(prefix="/api"))
    monkeypatch.setattr(routes, "effective_user", lambda request: "alice")
    manager = manager_module.SessionManager.__new__(manager_module.SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    monkeypatch.setattr(manager, "get_sessions_for_user", lambda owner: {})
    app = FastAPI()
    app.include_router(routes.setup_session_routes(manager, {}))
    with TestClient(app) as client:
        yield client, factory, manager
    engine.dispose()


def run_tidy(client, entrypoint):
    if entrypoint == "action":
        return asyncio.run(actions.run_auto_sort("alice", skip_llm=True))
    response = client.post("/api/sessions/auto-sort?skip_llm=true")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("entrypoint", ["route", "action"])
@pytest.mark.parametrize("title,prompt", [
    ("New chat", "Please analyze the following contract and retain all details. " * 20),
    ("Contract analysis", "Review contract"),
    ("Chat: analysis", "Explain these results and retain the full answer"),
    ("Test", "Here is a large input that should be analyzed and retained"),
], ids=["generic-title", "short-prompt", "title-prefix", "test-title"])
def test_tidy_preserves_nontrivial_saved_answer(tidy_api, title, prompt, entrypoint):
    client, factory, manager = tidy_api
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    answer = "Clause analysis and recommended changes.\n" * 2000
    with factory() as db:
        db.add(database.Session(
            id="valuable-chat", name=title, owner="alice", endpoint_url="", model="",
            archived=False, is_important=False, message_count=2,
            created_at=old, updated_at=old, last_accessed=old, last_message_at=old,
        ))
        db.commit()
        db.add_all([
            database.ChatMessage(id="prompt", session_id="valuable-chat", role="user",
                                 content=prompt, timestamp=old),
            database.ChatMessage(id="answer", session_id="valuable-chat", role="assistant",
                                 content=answer, timestamp=old + timedelta(seconds=1)),
        ])
        db.commit()
    run_tidy(client, entrypoint)
    with factory() as db:
        remaining = db.query(database.ChatMessage).filter_by(session_id="valuable-chat").count()
        survives = db.get(database.Session, "valuable-chat") is not None
    assert survives and remaining == 2, "Title/prompt heuristics must not erase saved answers"
    with factory() as db:
        assert db.get(database.ChatMessage, "answer").content == answer


@pytest.mark.parametrize("entrypoint", ["route", "action"])
@pytest.mark.parametrize("kind", ["unanswered", "greeting", "empty", "fresh-empty", "starred-empty", "archived", "other-owner", "incognito"])
def test_tidy_cleanup_boundaries(tidy_api, entrypoint, kind):
    client, factory, manager = tidy_api
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    timestamp = now if kind == "fresh-empty" else now - timedelta(hours=2)
    message = kind in {"unanswered", "greeting", "archived", "other-owner", "incognito"}
    with factory() as db:
        db.add(database.Session(
            id="boundary-chat", name="Incognito" if kind == "incognito" else "New chat",
            owner="bob" if kind == "other-owner" else "alice", endpoint_url="", model="",
            archived=kind == "archived", is_important=kind == "starred-empty",
            # Deliberately inaccurate metadata: cleanup must use message rows.
            message_count=0 if message else 40,
            created_at=timestamp, updated_at=timestamp, last_accessed=timestamp, last_message_at=timestamp,
        ))
        db.commit()
        if message:
            db.add(database.ChatMessage(id="boundary-message", session_id="boundary-chat", role="user",
                                        content="hi" if kind == "greeting" else "Pending question " * 100,
                                        timestamp=timestamp))
            db.commit()
    run_tidy(client, entrypoint)
    deleted = kind in {"empty", "incognito"}
    with factory() as db:
        assert (db.get(database.Session, "boundary-chat") is None) == deleted
        assert db.query(database.ChatMessage).filter_by(session_id="boundary-chat").count() == int(message and not deleted)


@pytest.mark.parametrize("entrypoint", ["route", "action"])
def test_tidy_keeps_folder_assignment_working(tidy_api, monkeypatch, entrypoint):
    from types import SimpleNamespace
    import src.llm_core as llm
    import src.task_endpoint as endpoints
    client, factory, manager = tidy_api
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    for sid in ["chat-one-long-id", "chat-two-long-id"]:
        with factory() as db:
            db.add(database.Session(id=sid, name="New chat", owner="alice", endpoint_url="", model="",
                                    archived=False, created_at=old, updated_at=old,
                                    last_accessed=old, last_message_at=old))
            db.commit()
            db.add(database.ChatMessage(id=f"{sid}-message", session_id=sid, role="user",
                                        content="Saved question", timestamp=old))
            db.commit()
        manager.sessions[sid] = SimpleNamespace(id=sid, name="New chat", archived=False,
                                                updated_at=old.isoformat(), created_at=old.isoformat())
    monkeypatch.setattr(manager, "get_sessions_for_user", lambda owner: manager.sessions)
    monkeypatch.setattr(endpoints, "resolve_task_endpoint", lambda **kwargs: ("http://model.invalid", "test", {}))
    response_text = '{"folders":{"Projects":["chat-one","chat-two"]}}'
    monkeypatch.setattr(llm, "llm_call", lambda *args, **kwargs: response_text)

    async def async_response(*args, **kwargs):
        return response_text

    monkeypatch.setattr(llm, "llm_call_async", async_response)
    if entrypoint == "action":
        result = asyncio.run(actions.run_auto_sort("alice"))
        assert "Sorted 2 sessions" in result
    else:
        response = client.post("/api/sessions/auto-sort")
        assert response.status_code == 200, response.text
        assert response.json()["updated"] == 2
    with factory() as db:
        assert db.query(database.ChatMessage).count() == 2
        assert {row.folder for row in db.query(database.Session).all()} == {"Projects"}
