"""Independent preference updates must not overwrite each other.

Preference writers used to load a user's whole map, change a key in it, and
hand the entire snapshot back to _save_for_user, which replaced that user's
record wholesale. Anything another writer had committed in between was lost:
both writes reported success, nothing was logged, and the file stayed valid
JSON.

The window is real because writers do not all run on the event loop.
src/caldav_sync.py runs its work through asyncio.to_thread specifically so the
loop stays free, so a sync worker can hold a stale snapshot across its network
I/O while a request handler writes a preference.

Locking only the write would not help, because the stale snapshot is taken
before the lock is ever acquired. _update_for_user re-reads and merges inside
the lock instead, so a writer only ever publishes its own keys.
"""
import asyncio
import json
import threading

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import routes.prefs_routes as pr


def _seed(tmp_path, monkeypatch, store):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps(store), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))
    return f


def _client_as(user):
    """A TestClient for the real prefs router, authenticated as `user`."""
    app = FastAPI()

    @app.middleware("http")
    async def _attach_user(request: Request, call_next):
        request.state.current_user = user
        return await call_next(request)

    app.include_router(pr.setup_prefs_routes())
    return TestClient(app)


def test_background_writer_does_not_revert_a_concurrent_preference_change(
    tmp_path, monkeypatch
):
    """The regression, in the shape production actually produces it.

    A CalDAV worker reads preferences, spends time on the network, then writes
    its accounts back. A preference change that lands inside that window must
    survive it.

    The window is opened deterministically rather than by timing: the worker's
    read is held on a gate, and the gate is only released once the competing
    write has either finished (old behaviour) or provably blocked on the lock
    (new behaviour).
    """
    from src import caldav_sync

    _seed(tmp_path, monkeypatch, {"_users": {"alice": {
        "theme": "light",
        "caldav": {"url": "https://dav.example.com", "username": "alice"},
    }}})

    gate = threading.Event()
    holding = threading.Event()
    real_load = pr._load
    first = {"done": False}

    def gated_load():
        """Hold the CalDAV worker inside its read, once."""
        if not first["done"] and threading.current_thread().name == "caldav":
            first["done"] = True
            data = real_load()
            holding.set()
            gate.wait(5)
            return data
        return real_load()

    monkeypatch.setattr(pr, "_load", gated_load)

    worker = threading.Thread(
        target=caldav_sync._load_caldav_accounts, args=("alice",), name="caldav"
    )
    worker.start()
    assert holding.wait(5), "CalDAV worker never reached its read"

    client = _client_as("alice")
    writer = threading.Thread(
        target=lambda: client.put("/api/prefs/theme", json={"value": "dark"})
    )
    writer.start()
    # Old behaviour: this write runs to completion inside the window. New
    # behaviour: it blocks on the lock the worker holds, so the join times out.
    writer.join(1.0)

    gate.set()
    worker.join(5)
    writer.join(5)

    stored = pr._load_for_user("alice")
    assert stored.get("theme") == "dark", (
        "the CalDAV worker republished a stale snapshot and reverted the "
        f"preference change; stored theme is {stored.get('theme')!r}"
    )
    assert stored.get("caldav_accounts"), "the CalDAV migration did not persist"


def test_concurrent_updates_keep_every_key(tmp_path, monkeypatch):
    """Many writers, distinct keys, no losses regardless of interleaving."""
    _seed(tmp_path, monkeypatch, {"_users": {"alice": {"theme": "dark"}}})

    count = 24
    start = threading.Barrier(count)
    errors = []

    def write(i):
        start.wait()
        try:
            pr._update_for_user("alice", {f"k{i}": i})
        except Exception as exc:
            errors.append((i, repr(exc)))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"{len(errors)} preference writes failed: {errors[:3]}"
    stored = pr._load_for_user("alice")
    lost = [f"k{i}" for i in range(count) if stored.get(f"k{i}") != i]
    assert lost == [], f"{len(lost)} of {count} writes were lost: {lost}"
    assert stored["theme"] == "dark", "a pre-existing preference was dropped"


def test_reads_and_writes_can_overlap_without_erroring(tmp_path, monkeypatch):
    """A read must never catch a write mid-swap.

    atomic_write_json replaces the file underneath readers. On Windows that
    replace fails with PermissionError while another thread holds the path
    open, so an unsynchronized reader turned a concurrent preference write
    into a 500. Reads take the same lock, so the two cannot overlap.
    """
    _seed(tmp_path, monkeypatch, {"_users": {"alice": {"theme": "dark"}}})

    workers = 12
    start = threading.Barrier(workers * 2)
    errors = []

    def write(i):
        start.wait()
        try:
            for _ in range(10):
                pr._update_for_user("alice", {f"k{i}": i})
        except Exception as exc:
            errors.append(("write", repr(exc)))

    def read(_i):
        start.wait()
        try:
            for _ in range(10):
                pr._load_for_user("alice")
        except Exception as exc:
            errors.append(("read", repr(exc)))

    threads = [threading.Thread(target=write, args=(i,)) for i in range(workers)]
    threads += [threading.Thread(target=read, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"overlapping preference access raised: {errors[:3]}"


def test_update_merges_into_the_currently_persisted_map(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, {"_users": {"alice": {"theme": "dark"}}})

    pr._update_for_user("alice", {"language": "fr"})
    returned = pr._update_for_user("alice", {"sidebar": "collapsed"})

    assert returned == {"theme": "dark", "language": "fr", "sidebar": "collapsed"}
    assert pr._load_for_user("alice") == returned


def test_update_removes_only_the_named_keys(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, {
        "_users": {"alice": {"theme": "dark", "caldav": {"url": "old"}}},
    })

    stored = pr._update_for_user(
        "alice", {"caldav_accounts": [{"url": "new"}]}, remove=("caldav",)
    )

    assert "caldav" not in stored
    assert stored["caldav_accounts"] == [{"url": "new"}]
    assert stored["theme"] == "dark"


def test_update_leaves_other_users_alone(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, {"_users": {
        "alice": {"theme": "dark"},
        "bob": {"theme": "paper"},
    }})

    pr._update_for_user("alice", {"language": "fr"})

    raw = pr._load()
    assert raw["_users"]["bob"] == {"theme": "paper"}
    assert raw["_users"]["alice"] == {"theme": "dark", "language": "fr"}


def test_auth_disabled_update_keeps_root_consent_and_other_users(tmp_path, monkeypatch):
    """The None path still honours the flat/root split for consent keys."""
    _seed(tmp_path, monkeypatch, {
        "_users": {"alice": {"theme": "light"}, "bob": {"theme": "paper"}},
        "foreground_fallback_enabled": True,
    })

    pr._update_for_user(None, {"theme": "dark"})

    raw = pr._load()
    assert raw["_users"]["bob"] == {"theme": "paper"}
    assert raw["_users"]["alice"]["theme"] == "dark"
    assert raw["foreground_fallback_enabled"] is True
    assert "foreground_fallback_enabled" not in raw["_users"]["alice"]


def test_tasks_onboarding_route_persists_and_reports_the_flags(tmp_path, monkeypatch):
    """The onboarding route is one of the converted callers and had no test.

    It writes tasks_opened/tasks_enabled and then reports them back, so it
    both patches preferences and reads its own result.
    """
    from types import SimpleNamespace

    import routes.task.task_routes as task_routes

    _seed(tmp_path, monkeypatch, {"_users": {"alice": {"theme": "dark"}}})

    async def _ensure_defaults(_owner):
        return None

    router = task_routes.setup_task_routes(
        SimpleNamespace(ensure_defaults=_ensure_defaults)
    )
    endpoint = None
    for route in router.routes:
        if getattr(route, "path", None) == "/api/tasks/onboarding" and "POST" in getattr(route, "methods", set()):
            endpoint = route.endpoint
    assert endpoint is not None, "POST /api/tasks/onboarding not registered"

    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    result = asyncio.run(endpoint(request, {"enabled": False}))

    assert result["opened"] is True
    assert result["enabled"] is False
    stored = pr._load_for_user("alice")
    assert stored["tasks_opened"] is True
    assert stored["theme"] == "dark", "onboarding dropped an unrelated preference"
