"""Route defaults must use manually pinned IDs without widening ownership."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as cdb
import routes.chat_routes as chat
from routes.task.task_routes import _resolve_run_endpoint
from routes.research.research_routes import _resolve_endpoint_runtime


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(chat, "SessionLocal", factory)
    with factory() as db:
        yield db
    engine.dispose()


def endpoint(db, eid="owned", *, owner="alice", cached=(), pinned=("pinned-chat",), hidden=()):
    row = cdb.ModelEndpoint(
        id=eid, name=eid, owner=owner, is_enabled=True,
        base_url=f"https://{eid}.example.test/v1", api_key=None,
        cached_models=json.dumps(cached), pinned_models=json.dumps(pinned),
        hidden_models=json.dumps(hidden),
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("cached", [(), ("hidden-chat",)])
def test_empty_session_recovers_a_visible_pinned_model(database, cached):
    ep = endpoint(database, cached=cached, hidden=("hidden-chat",))
    database.add(cdb.Session(id="chat", name="Chat", owner="alice", model="", endpoint_url=ep.base_url, endpoint_id=ep.id))
    database.commit()
    session = SimpleNamespace(model="", endpoint_url=ep.base_url, endpoint_id=ep.id)

    assert chat._recover_empty_session_model(session, "chat", "alice") is True
    assert session.model == "pinned-chat"
    database.expire_all()
    assert database.get(cdb.Session, "chat").model == "pinned-chat"


def test_empty_session_does_not_recover_a_hidden_pin(database):
    ep = endpoint(database, hidden=("pinned-chat",))
    session = SimpleNamespace(model="", endpoint_url=ep.base_url, endpoint_id=ep.id)
    assert chat._recover_empty_session_model(session, "missing", "alice") is False
    assert session.model == ""


def test_subscription_recovery_still_requires_its_live_catalog(database):
    ep = endpoint(database)
    ep.base_url = "https://chatgpt.com/backend-api/codex"
    database.commit()
    session = SimpleNamespace(model="", endpoint_url=ep.base_url, endpoint_id=ep.id)
    assert chat._recover_empty_session_model(session, "missing", "alice") is False
    assert session.model == ""


def test_task_run_reopens_against_its_owners_pinned_endpoint(database):
    endpoint(database, "foreign", owner="bob", cached=("pinned-chat",))
    own = endpoint(database)
    task = SimpleNamespace(owner="alice", endpoint_url="", session_id=None, model="pinned-chat")
    assert _resolve_run_endpoint(database, task, SimpleNamespace(model="pinned-chat")) == own.base_url


@pytest.mark.parametrize("owner", ["alice", None])
def test_task_run_can_find_visible_pins_on_owned_or_shared_endpoints(database, owner):
    ep = endpoint(database, owner=owner)
    task = SimpleNamespace(owner="alice", endpoint_url="", session_id=None, model="pinned-chat")
    assert _resolve_run_endpoint(database, task, SimpleNamespace(model=None)) == ep.base_url


def test_task_run_does_not_autoselect_a_hidden_pin(database):
    endpoint(database, hidden=("pinned-chat",))
    task = SimpleNamespace(owner="alice", endpoint_url="", session_id=None, model="pinned-chat")
    assert _resolve_run_endpoint(database, task, SimpleNamespace(model="pinned-chat")) == ""


@pytest.mark.parametrize("cached", [(), ("hidden-chat",)])
def test_research_default_uses_the_visible_pinned_id(database, cached):
    ep = endpoint(database, cached=cached, hidden=("hidden-chat",))
    assert _resolve_endpoint_runtime(ep, owner="alice") == (
        ep.base_url + "/chat/completions", "pinned-chat", {},
    )


def test_research_default_does_not_autoselect_a_hidden_pin(database):
    ep = endpoint(database, hidden=("pinned-chat",))
    assert _resolve_endpoint_runtime(ep, owner="alice") is None


def test_research_explicit_model_selection_is_preserved(database):
    ep = endpoint(database, cached=("cached-chat",))
    assert _resolve_endpoint_runtime(ep, owner="alice", model="explicit-chat")[1] == "explicit-chat"


def test_research_cached_default_keeps_priority_over_pins(database):
    ep = endpoint(database, cached=("cached-chat",))
    assert _resolve_endpoint_runtime(ep, owner="alice")[1] == "cached-chat"
