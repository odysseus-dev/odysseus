"""Persistent account routing must survive reloads and fail closed on deletion."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

import core.database as cdb
import core.session_manager as sm
import routes.chat_helpers as helpers
import routes.chat_routes as chat
import routes.session_routes as sessions
from src import endpoint_resolver

BASE = "https://chatgpt.com/backend-api/codex"


@pytest.fixture
def state(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (sm, helpers, chat, sessions, endpoint_resolver):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as db:
        for key, owner in (("a", "alice"), ("b", "alice"), ("c", "bob")):
            db.add(cdb.ModelEndpoint(id=key, owner=owner, name=key, base_url=BASE,
                                    provider_auth_id="auth-" + key, is_enabled=True,
                                    cached_models='["gpt-5.5"]', supports_tools=False))
        db.commit()
    manager = sm.SessionManager.__new__(sm.SessionManager)
    manager.sessions = {}
    manager.upload_handler = None
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime",
                        lambda ep, owner=None: (BASE, "secret-" + ep.id))
    yield factory, manager
    engine.dispose()


def _session(manager, binding="b"):
    return manager.create_session("s", "Chat", BASE + "/responses", "gpt-5.5",
                                  owner="alice", endpoint_id=binding)


def test_binding_survives_metadata_full_reload_and_sync(state):
    factory, manager = state
    _session(manager)
    with factory() as db:
        db.add(cdb.ChatMessage(id="message", session_id="s", role="user", content="Hello"))
        db.commit()
        row = db.get(cdb.Session, "s")
        assert manager._db_to_session_meta(row).endpoint_id == "b"
        assert manager._db_to_session(row, db).endpoint_id == "b"
        row.endpoint_id = "a"
        db.commit()
    manager.sync_session_metadata("s")
    assert manager.sessions["s"].endpoint_id == "a"


@pytest.mark.parametrize("disabled", [True, False])
def test_missing_or_disabled_bound_account_cannot_borrow_only_remaining_sibling(state, disabled):
    factory, manager = state
    sess = _session(manager)
    with factory() as db:
        endpoint = db.get(cdb.ModelEndpoint, "b")
        if disabled:
            endpoint.is_enabled = False
        else:
            db.delete(endpoint)
        db.get(cdb.Session, "s").headers = {"Authorization": "Bearer stale-secret"}
        db.commit()
    sess.headers = {"Authorization": "Bearer stale-secret"}
    helpers.resolve_session_auth(sess, "s", "alice")
    assert sess.headers == {}
    assert sess.endpoint_id == "b"
    assert chat._clear_orphaned_session_endpoint(sess, "alice") is True
    assert chat._recover_empty_session_model(sess, "s", "alice") is False
    with factory() as db:
        assert db.get(cdb.Session, "s").headers == {}
        assert db.get(cdb.Session, "s").endpoint_id == "b"


def test_legacy_binding_is_persisted_even_when_authentication_fails(state, monkeypatch):
    factory, manager = state
    sess = _session(manager, None)
    def unavailable(*args, **kwargs):
        raise RuntimeError("credentials unavailable")
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint_runtime", unavailable)
    helpers.resolve_session_auth(sess, "s", "alice")
    assert sess.endpoint_id == "a"
    with factory() as db:
        assert db.get(cdb.Session, "s").endpoint_id == "a"


def test_explicit_selection_switches_same_url_and_model_binding(state):
    factory, manager = state
    sess = _session(manager)
    assert chat._reconcile_selected_route_from_request(None, sess, "s", {
        "selected_model": "gpt-5.5", "selected_endpoint_id": "a",
    }, "alice")
    assert sess.endpoint_id == "a"
    with factory() as db:
        assert db.get(cdb.Session, "s").endpoint_id == "a"
    assert not chat._reconcile_selected_route_from_request(None, sess, "s", {
        "selected_model": "gpt-5.5", "selected_endpoint_id": "c",
    }, "alice")
    assert sess.endpoint_id == "a"


def test_url_only_model_change_retains_exact_binding(state):
    factory, manager = state
    sess = _session(manager)
    assert chat._reconcile_selected_route_from_request(None, sess, "s", {
        "selected_model": "another-model", "selected_endpoint_url": BASE,
    }, "alice")
    assert sess.endpoint_id == "b"


def test_session_patch_and_unrelated_rename_preserve_binding(state, monkeypatch):
    factory, manager = state
    sess = _session(manager)
    monkeypatch.setattr(sessions, "_verify_session_owner", lambda *args: None)
    router = sessions.setup_session_routes(manager, {})
    patch = [r.endpoint for r in router.routes if r.path == "/api/session/{sid}" and "PATCH" in r.methods][-1]
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    kwargs = dict(request=request, sid="s", name=None, folder=None, cwd=None)
    patch(**kwargs, model="gpt-5.5", endpoint_url=BASE, endpoint_id="a")
    assert sess.endpoint_id == "a"
    with factory() as db:
        assert db.get(cdb.Session, "s").endpoint_id == "a"
    with pytest.raises(HTTPException):
        patch(**kwargs, model="gpt-5.5", endpoint_url=BASE, endpoint_id="c")
    patch(**kwargs, model=None, endpoint_url=None, endpoint_id=None)
    assert sess.endpoint_id == "a"


def test_session_list_reports_bound_account_for_duplicate_model_and_url(state, monkeypatch):
    factory, manager = state
    sess = _session(manager)
    manager.get_sessions_for_user = lambda *args, **kwargs: {"s": sess}
    router = sessions.setup_session_routes(manager, {})
    listing = [r.endpoint for r in router.routes if r.path == "/api/sessions" and "GET" in r.methods][-1]
    rows = listing(SimpleNamespace(state=SimpleNamespace(current_user="alice"), query_params={}))
    assert rows[0]["endpoint_id"] == "b"
    assert rows[0]["endpoint_name"] == "b"


def test_migration_is_additive_idempotent_and_repairs_missing_index(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE sessions (id VARCHAR PRIMARY KEY, model VARCHAR)"))
        db.execute(text("INSERT INTO sessions VALUES ('legacy', 'gpt-5.5')"))
    monkeypatch.setattr(cdb, "engine", engine)
    cdb._migrate_add_session_endpoint_id_column()
    cdb._migrate_add_session_endpoint_id_column()
    with engine.begin() as db:
        assert db.execute(text("SELECT model, endpoint_id FROM sessions")).one() == ("gpt-5.5", None)
        db.execute(text("DROP INDEX ix_sessions_endpoint_id"))
    cdb._migrate_add_session_endpoint_id_column()
    assert [idx["name"] for idx in inspect(engine).get_indexes("sessions")] == ["ix_sessions_endpoint_id"]
    engine.dispose()
