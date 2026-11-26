"""Notes writes and account deletion take their locks in one order.

Account deletion takes the account's lifecycle lock exclusively, then
cleans the database. Every Notes write must take the same lock (shared)
before its first SQL write and hold it until its commit or rollback;
otherwise a write holding a database lock waits for the lifecycle lock while
the deletion holding the lifecycle lock waits for the database (#7376
review, P1). The database cannot see that cycle, so it only ends on a
statement or busy timeout.

Each case runs a real Notes write (route or agent tool) and the real admin
deletion route in two threads, paused at SQL statements, in both orders:

- the write lands first: the deletion waits on the lifecycle lock (it runs
  no SQL until the write commits) and then purges the written note;
- the deletion goes first: the write waits before any SQL statement and is
  then refused, writing nothing.

SQLite always; PostgreSQL when ODYSSEUS_TEST_PG_URL names a server (each
test gets its own schema).
"""
import asyncio
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.auth as ca
import core.database as cdb
import routes.note.note_routes as nr
from core.auth import AuthManager
from core.database import Note, NoteShare, NoteVersion
from core.note_writes import note_body_revision
from routes.auth_routes import DeleteUserRequest, SESSION_COOKIE, setup_auth_routes
from src.tools.notes import do_manage_notes

# Long enough for a loaded CI runner, short enough that an inversion fails
# the test instead of hanging it.
WAIT = 10.0
# How long a blocked thread is given to (wrongly) run SQL before we look.
SETTLE = 0.5
_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE")


def _sqlite_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/lock-order.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _short_busy_timeout(dbapi_connection, _record):
        # After core.database's PRAGMAs: an inversion fails in seconds.
        dbapi_connection.execute("PRAGMA busy_timeout=3000")

    return engine, lambda: None


def _postgres_engine():
    url = os.environ.get("ODYSSEUS_TEST_PG_URL")
    if not url:
        pytest.skip("ODYSSEUS_TEST_PG_URL environment variable not set")
    schema = f"lock_order_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url, poolclass=NullPool)
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:
        pytest.skip(f"PostgreSQL server not reachable: {exc}")
    engine = create_engine(
        url,
        poolclass=NullPool,
        # statement_timeout ends an inversion instead of hanging the run.
        connect_args={"options": f"-csearch_path={schema} -cstatement_timeout=8000"},
    )

    def drop():
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()

    return engine, drop


class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


class _Pauses:
    """Pause a thread at a chosen point, and record which SQL writes each thread ran.

    Threads are named by `role`; any other thread (the worker Starlette runs
    a sync route in) takes the `adopt` name once set.
    """

    def __init__(self, engine, mgr):
        self.lock = threading.Lock()
        self.writes = {}          # role -> [statement verbs]
        self.roles = {}           # thread id -> role
        self.gates = {}           # (role, point) -> (match, reached, resume)
        self.adopted = None
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)
        original = mgr.account_mutation_guard

        @contextmanager
        def guard(*args, **kwargs):
            self._maybe_pause("lease", "")
            with original(*args, **kwargs):
                yield
        mgr.account_mutation_guard = guard

    def role(self, name):
        with self.lock:
            self.roles[threading.get_ident()] = name
            self.writes.setdefault(name, [])

    def adopt(self, name):
        with self.lock:
            self.adopted = name
            self.writes.setdefault(name, [])

    def pause(self, name, point, match=""):
        """Pause `name` the first time it reaches `point`.

        "write": right after its first SQL write containing `match` ran,
        holding that write's database lock. "lease": right before it asks
        for the account lifecycle lock.
        """
        reached, resume = threading.Event(), threading.Event()
        self.gates[(name, point)] = (match, reached, resume)
        return reached, resume

    def _role(self):
        tid = threading.get_ident()
        name = self.roles.get(tid)
        if name is None and self.adopted:
            name = self.roles[tid] = self.adopted
        return name

    def _maybe_pause(self, point, statement):
        with self.lock:
            name = self._role()
            gate = self.gates.get((name, point))
            if gate is None or gate[0] not in statement:
                return
            del self.gates[(name, point)]
        _match, reached, resume = gate
        reached.set()
        assert resume.wait(WAIT), f"{name} was never resumed"

    def _before(self, conn, cursor, statement, params, context, executemany):
        verb = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if verb not in _WRITE_VERBS:
            return
        with self.lock:
            name = self._role()
            if name is not None:
                self.writes.setdefault(name, []).append(verb)

    def _after(self, conn, cursor, statement, params, context, executemany):
        # Once the write ran, so the thread holds its database lock.
        verb = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        if verb in _WRITE_VERBS:
            self._maybe_pause("write", statement)


@pytest.fixture(params=["sqlite", "postgresql"])
def env(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        engine, drop = _sqlite_engine(tmp_path)
    else:
        engine, drop = _postgres_engine()
    try:
        cdb.Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

        @contextmanager
        def _get_db():
            s = factory()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(cdb, "get_db_session", _get_db)
        monkeypatch.setattr(nr, "SessionLocal", factory)

        mgr = AuthManager(auth_path=str(tmp_path / "auth.json"))
        mgr.create_user("admin", "admin-pass-123", is_admin=True)
        mgr.create_user("alice", "alice-pass-123")
        mgr.create_user("bob", "bob-pass-123")
        monkeypatch.setattr(ca, "_DEFAULT_AUTH_MANAGER", mgr)

        app = FastAPI()
        app.state.auth_manager = mgr
        app.include_router(nr.setup_note_routes())

        delete_route = next(
            r.endpoint for r in setup_auth_routes(mgr).routes
            if getattr(r, "path", "") == "/api/auth/users" and "DELETE" in getattr(r, "methods", set())
        )
        pauses = _Pauses(engine, mgr)
        yield SimpleNamespace(backend=request.param, factory=factory, mgr=mgr, app=app, pauses=pauses,
                              delete_route=delete_route)
    finally:
        drop()


def _seed(env, note_id="n1", owner="alice", content="- [ ] milk\n- [ ] eggs", share_with="bob"):
    with env.factory() as db:
        db.add(Note(id=note_id, owner=owner, title=note_id, content=content))
        if share_with:
            db.add(NoteShare(id=f"{note_id}-{share_with}", note_id=note_id, shared_with=share_with, permission="edit"))
        db.commit()
        return note_body_revision(db.get(Note, note_id))


def _rows(env, model, **where):
    with env.factory() as db:
        q = db.query(model)
        for key, value in where.items():
            q = q.filter(getattr(model, key) == value)
        return q.count()


def _call_route(env, method, url, user="alice", **kw):
    async def go():
        transport = httpx.ASGITransport(app=_Identity(env.app), client=("203.0.113.7", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
            return await c.request(method, url, headers={"x-test-user": user}, **kw)
    return asyncio.run(go())


def _delete_account(env, username):
    handler = env.delete_route
    token = env.mgr.create_session("admin", "admin-pass-123")
    req = SimpleNamespace(
        cookies={SESSION_COOKIE: token},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=env.mgr, invalidate_token_cache=lambda: None)),
    )
    return asyncio.run(handler(DeleteUserRequest(username=username), req))


# --- the writes -------------------------------------------------------------
# Each returns True when the write landed and False when it was refused.

def _route_update(env, rev):
    r = _call_route(env, "PUT", "/api/notes/n1", json={"content": "- [ ] milk\n- [ ] eggs\nmore", "body_revision": rev})
    assert r.status_code in (200, 403), r.text
    return r.status_code == 200


def _route_create(env, rev):
    r = _call_route(env, "POST", "/api/notes", json={"title": "fresh", "content": "new"})
    assert r.status_code in (200, 403), r.text
    return r.status_code == 200


def _route_delete(env, rev):
    r = _call_route(env, "DELETE", "/api/notes/n1")
    assert r.status_code in (200, 403), r.text
    return r.status_code == 200


def _route_unshare(env, rev):
    r = _call_route(env, "DELETE", "/api/notes/n1/share/bob")
    assert r.status_code in (200, 403), r.text
    return r.status_code == 200


def _route_toggle(env, rev):
    r = _call_route(env, "POST", "/api/notes/n1/items/0/toggle", json={"body_revision": rev})
    assert r.status_code in (200, 403), r.text
    return r.status_code == 200


def _agent(args):
    out = asyncio.run(do_manage_notes(json.dumps(args), owner="alice"))
    return out.get("exit_code") == 0


def _agent_update(env, rev):
    return _agent({"action": "update", "id": "n1", "content": "agent text", "body_revision": rev})


def _agent_toggle(env, rev):
    return _agent({"action": "toggle_item", "id": "n1", "index": 0, "body_revision": rev})


def _agent_delete(env, rev):
    return _agent({"action": "delete", "id": "n1"})


WRITES = {
    "route_update": _route_update,
    "route_create": _route_create,
    "route_delete": _route_delete,
    "route_unshare": _route_unshare,
    "route_toggle": _route_toggle,
    "agent_update": _agent_update,
    "agent_toggle": _agent_toggle,
    "agent_delete": _agent_delete,
}


def _thread(target, results, key, role, env):
    def run():
        env.pauses.role(role)
        try:
            results[key] = target()
        except BaseException as exc:  # reported by the assertions below
            results[key] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _wait_deletion_at_lock(env, username):
    """Until the deletion is queued for, or holds, the account's lifecycle lock."""
    lock = env.mgr._get_user_lifecycle_lock(username)
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        with lock._lock:
            if lock._writer_waiting or lock._writers:
                break
        time.sleep(0.01)
    else:
        raise AssertionError("the deletion never reached the lifecycle lock")
    # Give it the chance to (wrongly) go on to SQL.
    time.sleep(SETTLE)


def _assert_alice_gone(env):
    with env.factory() as db:
        assert db.query(Note).filter(Note.owner == "alice").count() == 0
        assert db.query(NoteVersion).filter(NoteVersion.owner == "alice").count() == 0
    assert "alice" not in env.mgr.users
    assert "alice" not in env.mgr.deleting_users


@pytest.mark.parametrize("write", sorted(WRITES))
def test_write_lands_first_then_deletion_purges_it(env, write):
    rev = _seed(env)
    results = {}
    env.pauses.adopt("note")
    reached, resume = env.pauses.pause("note", "write")
    t_note = _thread(lambda: WRITES[write](env, rev), results, "note", "note", env)
    assert reached.wait(WAIT), "the write never reached SQL"

    t_del = _thread(lambda: _delete_account(env, "alice"), results, "delete", "delete", env)
    _wait_deletion_at_lock(env, "alice")
    # The deletion waits on the lifecycle lock, not on the database.
    assert env.pauses.writes.get("delete") == [], env.pauses.writes
    assert t_del.is_alive()

    resume.set()
    t_note.join(WAIT)
    t_del.join(WAIT)
    assert not t_note.is_alive() and not t_del.is_alive(), "lock inversion: a thread never finished"
    assert results["note"] is True, results
    assert results["delete"] == {"ok": True}, results
    _assert_alice_gone(env)


@pytest.mark.parametrize("write", sorted(WRITES))
def test_deletion_first_then_write_is_refused(env, write):
    rev = _seed(env)
    results = {}
    # The write was admitted while alice was active, and is about to lock.
    env.pauses.adopt("note")
    note_at_lease, note_go = env.pauses.pause("note", "lease")
    t_note = _thread(lambda: WRITES[write](env, rev), results, "note", "note", env)
    assert note_at_lease.wait(WAIT), "the write never asked for its lease"
    assert env.pauses.writes["note"] == []

    # The deletion takes alice's lock and starts purging her notes.
    del_at_purge, del_go = env.pauses.pause("delete", "write", match="note")
    t_del = _thread(lambda: _delete_account(env, "alice"), results, "delete", "delete", env)
    assert del_at_purge.wait(WAIT), "the deletion never reached the notes purge"

    note_go.set()
    time.sleep(SETTLE)
    # The write waits on the lifecycle lock before running any SQL write.
    assert t_note.is_alive()
    assert env.pauses.writes["note"] == [], env.pauses.writes

    del_go.set()
    t_del.join(WAIT)
    t_note.join(WAIT)
    assert not t_note.is_alive() and not t_del.is_alive(), "lock inversion: a thread never finished"
    assert results["delete"] == {"ok": True}, results
    assert results["note"] is False, results
    assert env.pauses.writes.get("note") == [], env.pauses.writes
    _assert_alice_gone(env)


def test_bob_shares_survive_alice_deletion_racing_a_share_write(env):
    """A grant write names both accounts; deleting one leaves the other's notes alone."""
    _seed(env, note_id="b1", owner="bob", content="bob's", share_with=None)
    results = {}
    env.pauses.adopt("note")
    reached, resume = env.pauses.pause("note", "write")
    t_note = _thread(
        lambda: _call_route(env, "POST", "/api/notes/b1/share", user="bob",
                            json={"users": ["alice"], "permission": "edit"}).status_code,
        results, "note", "note", env)
    assert reached.wait(WAIT)
    t_del = _thread(lambda: _delete_account(env, "alice"), results, "delete", "delete", env)
    _wait_deletion_at_lock(env, "alice")
    assert env.pauses.writes.get("delete") == []
    resume.set()
    t_note.join(WAIT)
    t_del.join(WAIT)
    assert results == {"note": 200, "delete": {"ok": True}}, results
    assert _rows(env, Note, id="b1") == 1
    # The grant to alice landed first, so her deletion removed it.
    assert _rows(env, NoteShare, note_id="b1") == 0


def test_failed_write_rolls_back_and_releases_the_lock(env, monkeypatch):
    """A write that fails after its first SQL statement leaves nothing and no lock behind."""
    rev = _seed(env)

    def boom(*_a, **_k):
        raise RuntimeError("upload store down")

    # After record_version and the body guard have both written.
    monkeypatch.setattr(nr, "commit_if_unchanged", boom)
    with pytest.raises(RuntimeError):
        _route_update(env, rev)
    with env.factory() as db:
        assert db.get(Note, "n1").content == "- [ ] milk\n- [ ] eggs"
        assert db.query(NoteVersion).count() == 0

    # The lock is free: an exclusive holder gets it at once.
    got = threading.Event()

    def take():
        with env.mgr.account_deletion_guard("alice"):
            got.set()
    t = threading.Thread(target=take, daemon=True)
    t.start()
    assert got.wait(2.0), "the lifecycle lock was not released"
    t.join(WAIT)
    assert _delete_account(env, "alice") == {"ok": True}
    _assert_alice_gone(env)


def test_refused_lease_runs_no_sql_and_releases(env):
    """An inactive account is refused before any write, and the refusal holds no lock."""
    _seed(env)
    env.pauses.role("main")
    with env.mgr._cross_process_lock():
        env.mgr._config.setdefault("deleting", {})["alice"] = env.mgr._config["users"]["alice"]
        env.mgr._save()
    assert _agent_update(env, None) is False
    assert env.pauses.writes["main"] == []
    with env.factory() as db:
        assert db.get(Note, "n1").content == "- [ ] milk\n- [ ] eggs"
    lock = env.mgr._get_user_lifecycle_lock("alice")
    assert lock._readers == 0 and lock._writers == 0


def test_writer_thread_can_take_its_own_read_lock(env):
    """Deletion code that calls a Notes helper must not wait for itself."""
    done = threading.Event()

    def run():
        with env.mgr.account_deletion_guard("alice"):
            with env.mgr._get_user_lifecycle_lock("alice").read_lock():
                done.set()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert done.wait(2.0)
    t.join(WAIT)
    lock = env.mgr._get_user_lifecycle_lock("alice")
    assert lock._readers == 0 and lock._writers == 0
