import asyncio
import sys
import tempfile
import types
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import GroupChatState, Session as DbSession

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


class _JsonRequest:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


def _stub_multipart_if_missing(monkeypatch):
    try:
        import python_multipart  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("python_multipart")
    stub.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", stub)


def _reset_db():
    db = _TS()
    try:
        db.query(GroupChatState).delete()
        db.query(DbSession).delete()
        db.commit()
    finally:
        db.close()


def _add_session(session_id, owner="alice", name="session"):
    db = _TS()
    try:
        db.add(DbSession(
            id=session_id,
            owner=owner,
            name=name,
            endpoint_url="http://localhost:11434",
            model="llama3",
            archived=False,
        ))
        db.commit()
    finally:
        db.close()


def _endpoint(router, path, method):
    return next(
        r.endpoint for r in reversed(router.routes)
        if getattr(r, "path", "") == path and method in getattr(r, "methods", set())
    )


def _routes(monkeypatch, session_manager=None):
    import routes.session_routes as sr

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: "alice")
    return sr.setup_session_routes(session_manager or MagicMock(), {})


def _session_stub(session_id, name, archived=False):
    return types.SimpleNamespace(
        id=session_id,
        name=name,
        model="llama3",
        endpoint_url="http://localhost:11434",
        rag=False,
        archived=archived,
    )


def _group_payload(parent_id, participant_ids):
    return {
        "active": True,
        "mode": "round-robin",
        "models": [
            {
                "mid": "llama3",
                "display": "Llama 3",
                "url": "http://localhost:11434",
                "endpointId": "local",
                "_groupName": "Athena",
                "character": {
                    "characterId": "athena",
                    "characterName": "Athena",
                    "characterPrompt": "Offer wise counsel.",
                },
            },
            {
                "mid": "mistral",
                "display": "Mistral",
                "url": "http://localhost:11434",
                "_groupName": "Mistral",
            },
        ],
        "participantSessions": participant_ids,
        "parentSessionId": "client-side-placeholder",
        "roundRobinIdx": 3,
    }


def _add_group_state(parent_id, participant_ids, owner="alice"):
    db = _TS()
    try:
        db.add(GroupChatState(
            parent_session_id=parent_id,
            owner=owner,
            mode="round-robin",
            state=_group_payload(parent_id, participant_ids) | {"parentSessionId": parent_id},
        ))
        db.commit()
    finally:
        db.close()


def test_group_chat_state_round_trips_personas_and_participant_sessions(monkeypatch):
    _reset_db()
    router = _routes(monkeypatch)
    parent_id = str(uuid.uuid4())
    participant_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    _add_session(parent_id, name="parent")
    for session_id in participant_ids:
        _add_session(session_id, name="participant")

    save = _endpoint(router, "/api/session/{sid}/group_state", "PUT")
    get = _endpoint(router, "/api/session/{sid}/group_state", "GET")

    saved = asyncio.run(save(request=_JsonRequest(_group_payload(parent_id, participant_ids)), sid=parent_id))
    restored = get(request=MagicMock(), sid=parent_id)

    assert saved["ok"] is True
    assert restored["ok"] is True
    state = restored["group_state"]
    assert state["parentSessionId"] == parent_id
    assert state["mode"] == "round-robin"
    assert state["participantSessions"] == participant_ids
    assert state["models"][0]["_groupName"] == "Athena"
    assert state["models"][0]["character"]["characterPrompt"] == "Offer wise counsel."


def test_list_sessions_hides_group_participants(monkeypatch):
    _reset_db()
    parent_id = str(uuid.uuid4())
    child_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    normal_id = str(uuid.uuid4())
    _add_session(parent_id, name="[GRP] Athena, Mistral")
    for session_id in child_ids:
        _add_session(session_id, name="[GRP] participant")
    _add_session(normal_id, name="normal chat")
    _add_group_state(parent_id, child_ids)

    sm = MagicMock()
    sm.get_sessions_for_user.return_value = {
        parent_id: _session_stub(parent_id, "[GRP] Athena, Mistral"),
        child_ids[0]: _session_stub(child_ids[0], "[GRP] Athena"),
        child_ids[1]: _session_stub(child_ids[1], "[GRP] Mistral"),
        normal_id: _session_stub(normal_id, "normal chat"),
    }
    router = _routes(monkeypatch, sm)
    list_sessions = _endpoint(router, "/api/sessions", "GET")

    returned_ids = {session["id"] for session in list_sessions(request=MagicMock())}

    assert parent_id in returned_ids
    assert normal_id in returned_ids
    assert not set(child_ids) & returned_ids


def test_group_parent_folder_move_cascades_and_child_move_is_blocked(monkeypatch):
    _reset_db()
    parent_id = str(uuid.uuid4())
    child_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    _add_session(parent_id, name="[GRP] Athena, Mistral")
    for session_id in child_ids:
        _add_session(session_id, name="[GRP] participant")
    _add_group_state(parent_id, child_ids)

    sm = MagicMock()
    sm.get_session.return_value = _session_stub(parent_id, "[GRP] Athena, Mistral")
    router = _routes(monkeypatch, sm)
    patch_session = _endpoint(router, "/api/session/{sid}", "PATCH")

    result = patch_session(
        request=MagicMock(),
        sid=parent_id,
        name=None,
        folder="Research",
        model=None,
        endpoint_url=None,
        endpoint_id=None,
    )

    db = _TS()
    try:
        folders = {
            row.id: row.folder
            for row in db.query(DbSession).filter(DbSession.id.in_([parent_id, *child_ids])).all()
        }
    finally:
        db.close()
    assert result["folder"] == "Research"
    assert folders == {parent_id: "Research", child_ids[0]: "Research", child_ids[1]: "Research"}

    with pytest.raises(HTTPException) as exc:
        patch_session(
            request=MagicMock(),
            sid=child_ids[0],
            name=None,
            folder="Solo",
            model=None,
            endpoint_url=None,
            endpoint_id=None,
        )
    assert exc.value.status_code == 403

    db = _TS()
    try:
        child = db.query(DbSession).filter(DbSession.id == child_ids[0]).first()
        assert child.folder == "Research"
    finally:
        db.close()


def test_group_chat_state_rejects_participants_from_other_users(monkeypatch):
    _reset_db()
    router = _routes(monkeypatch)
    parent_id = str(uuid.uuid4())
    alice_participant = str(uuid.uuid4())
    bob_participant = str(uuid.uuid4())
    _add_session(parent_id, name="parent")
    _add_session(alice_participant, name="alice participant")
    _add_session(bob_participant, owner="bob", name="bob participant")

    save = _endpoint(router, "/api/session/{sid}/group_state", "PUT")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(save(
            request=_JsonRequest(_group_payload(parent_id, [alice_participant, bob_participant])),
            sid=parent_id,
        ))
    assert exc.value.status_code == 404


def test_missing_group_chat_state_returns_empty_result(monkeypatch):
    _reset_db()
    router = _routes(monkeypatch)
    parent_id = str(uuid.uuid4())
    _add_session(parent_id, name="parent")

    get = _endpoint(router, "/api/session/{sid}/group_state", "GET")

    assert get(request=MagicMock(), sid=parent_id) == {"ok": False, "group_state": None}
