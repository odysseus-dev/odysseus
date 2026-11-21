"""Deterministic tests for preference persistence and concurrency.

Validates:
- Multi-field atomic writes (PATCH /api/prefs)
- Scenario A: Same user, concurrent updates to different keys
- Scenario B: Different users, concurrent updates
- Scenario C: Same user, overlapping PATCH (multi-field) and PUT (single-field)
- Scenario D: Auth-disabled storage (flat format & multi-user preservation)
- Scenario E: Failure recovery & corruption prevention
- Multi-worker / multi-process safety via store_transaction
- Re-entrancy of store_transaction
- Event-loop responsiveness, cancellation, and failure cleanup on the async path
"""

import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.atomic_io import store_transaction
import routes.prefs_routes as prefs_routes


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "default_model": "alpha"},
            "bob": {"theme": "paper", "default_model": "beta"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    return prefs_file


@pytest.fixture
def app_and_client(isolated_prefs, monkeypatch):
    app = FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    active_user = {"user": "alice"}
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda req: active_user["user"])
    return app, active_user, isolated_prefs


# ── 1. Multi-field Atomicity ──

def test_patch_multi_field_atomic_success(app_and_client):
    app, active_user, prefs_file = app_and_client
    client = TestClient(app)
    active_user["user"] = "alice"

    resp = client.patch("/api/prefs", json={"values": {
        "default_endpoint_id": "ep-1",
        "default_model": "model-1",
        "theme": "dark",
    }})
    assert resp.status_code == 200
    assert resp.json() == {"values": {
        "default_endpoint_id": "ep-1",
        "default_model": "model-1",
        "theme": "dark",
    }}

    data = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    assert data["default_endpoint_id"] == "ep-1"
    assert data["default_model"] == "model-1"
    assert data["theme"] == "dark"


def test_patch_multi_field_atomic_rejects_invalid_without_partial_write(app_and_client):
    app, active_user, prefs_file = app_and_client
    client = TestClient(app)
    active_user["user"] = "alice"
    before = prefs_file.read_text(encoding="utf-8")

    # Empty object
    resp = client.patch("/api/prefs", json={"values": {}})
    assert resp.status_code == 400
    assert prefs_file.read_text(encoding="utf-8") == before

    # Non-string keys
    resp = client.patch("/api/prefs", json={"values": {"": "bad"}})
    assert resp.status_code == 400
    assert prefs_file.read_text(encoding="utf-8") == before

    # Malformed values (list instead of dict)
    resp = client.patch("/api/prefs", json={"values": ["not-a-dict"]})
    assert resp.status_code == 400
    assert prefs_file.read_text(encoding="utf-8") == before


# ── 2. Scenario A: Same User, Different Keys Concurrency ──

def test_concurrent_same_user_different_keys_via_threads(app_and_client):
    app, active_user, prefs_file = app_and_client
    active_user["user"] = "alice"

    barrier = threading.Barrier(2)

    def update_key(key, val):
        client = TestClient(app)
        barrier.wait()
        return client.put(f"/api/prefs/{key}", json={"value": val})

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(update_key, "key_a", "val_a")
        f2 = executor.submit(update_key, "key_b", "val_b")
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code == 200
    assert r2.status_code == 200

    data = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    assert data["key_a"] == "val_a"
    assert data["key_b"] == "val_b"
    assert data["theme"] == "light"  # Unrelated key preserved


# ── 3. Scenario B: Different Users Concurrency ──

def test_concurrent_different_users_via_threads(app_and_client, monkeypatch):
    app, _, prefs_file = app_and_client

    barrier = threading.Barrier(2)

    def worker_alice():
        client = TestClient(app)
        barrier.wait()
        return client.patch("/api/prefs", json={"values": {"color": "blue"}}, headers={"X-User": "alice"})

    def worker_bob():
        client = TestClient(app)
        barrier.wait()
        return client.patch("/api/prefs", json={"values": {"font": "sans"}}, headers={"X-User": "bob"})

    monkeypatch.setattr(prefs_routes, "get_current_user", lambda req: req.headers.get("X-User"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        fa = executor.submit(worker_alice)
        fb = executor.submit(worker_bob)
        ra = fa.result()
        rb = fb.result()

    assert ra.status_code == 200
    assert rb.status_code == 200

    users = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]
    assert users["alice"]["color"] == "blue"
    assert users["alice"]["theme"] == "light"
    assert users["bob"]["font"] == "sans"
    assert users["bob"]["theme"] == "paper"


# ── 4. Scenario C: Same User, PATCH + PUT Overlap ──

def test_concurrent_same_user_patch_and_put_overlap(app_and_client):
    app, active_user, prefs_file = app_and_client
    active_user["user"] = "alice"

    barrier = threading.Barrier(2)

    def do_patch():
        client = TestClient(app)
        barrier.wait()
        return client.patch("/api/prefs", json={"values": {
            "default_endpoint_id": "ep-new",
            "default_model": "model-new",
        }})

    def do_put():
        client = TestClient(app)
        barrier.wait()
        return client.put("/api/prefs/sidebar_width", json={"value": 260})

    with ThreadPoolExecutor(max_workers=2) as executor:
        fp = executor.submit(do_patch)
        fu = executor.submit(do_put)
        rp = fp.result()
        ru = fu.result()

    assert rp.status_code == 200
    assert ru.status_code == 200

    alice = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    assert alice["default_endpoint_id"] == "ep-new"
    assert alice["default_model"] == "model-new"
    assert alice["sidebar_width"] == 260
    assert alice["theme"] == "light"


# ── 5. Scenario D: Auth-Disabled Storage Semantics ──

def test_auth_disabled_single_user_and_named_preservation(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    # Multi-user file where auth is now disabled
    f.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "foreground_fallback_enabled": True},
            "bob": {"theme": "paper"},
        },
        "foreground_fallback_enabled": False,
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))

    # Read with auth disabled -> gets alice (first user) + root foreground
    read_data = prefs_routes._load_for_user(None)
    assert read_data["theme"] == "light"
    assert read_data["foreground_fallback_enabled"] is False

    # Update with auth disabled
    prefs_routes._update_for_user(None, {"theme": "solarized", "foreground_fallback_enabled": True})

    data = json.loads(f.read_text(encoding="utf-8"))
    # alice received non-foreground update
    assert data["_users"]["alice"]["theme"] == "solarized"
    # alice named foreground was untouched
    assert data["_users"]["alice"]["foreground_fallback_enabled"] is True
    # root received auth-disabled foreground update
    assert data["foreground_fallback_enabled"] is True
    # bob was untouched
    assert data["_users"]["bob"]["theme"] == "paper"


def test_auth_disabled_legacy_flat_store_updates_flat(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"theme": "light", "editor": "vim"}), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))

    prefs_routes._update_for_user(None, {"theme": "dark", "editor": None})

    data = json.loads(f.read_text(encoding="utf-8"))
    assert data == {"theme": "dark"}
    assert "_users" not in data


# ── 6. Scenario E: Failure Recovery & File Integrity ──

def test_failure_during_save_leaves_file_intact(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"_users": {"alice": {"theme": "light"}}}), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(f))

    # Simulate an error during atomic write serialization
    import core.atomic_io as atomic_io
    real_dump = atomic_io.json.dump

    def failing_dump(obj, fp, **kwargs):
        raise OSError("Disk write error")

    monkeypatch.setattr(atomic_io.json, "dump", failing_dump)

    with pytest.raises(OSError, match="Disk write error"):
        prefs_routes._update_for_user("alice", {"theme": "corrupted"})

    # Original file is completely intact
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data == {"_users": {"alice": {"theme": "light"}}}


# ── 7. Re-entrancy of store_transaction ──

def test_store_transaction_reentrant_on_same_thread(tmp_path):
    target = tmp_path / "test.json"

    @store_transaction(lambda: target)
    def inner_step():
        return "inner-done"

    @store_transaction(lambda: target)
    def outer_step():
        return inner_step()

    # Must complete without deadlock
    result = outer_step()
    assert result == "inner-done"


# ── 8. Cross-Process / Multi-Worker Concurrency ──

def _mp_worker(prefs_path_str, user_name, key, val, barrier):
    import routes.prefs_routes as pr
    pr.PREFS_FILE = prefs_path_str
    barrier.wait()
    pr._update_for_user(user_name, {key: val})


def test_cross_process_concurrent_updates(tmp_path):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "worker_a": {"init": 1},
            "worker_b": {"init": 2},
        }
    }), encoding="utf-8")

    barrier = multiprocessing.Barrier(2)
    p1 = multiprocessing.Process(
        target=_mp_worker,
        args=(str(prefs_file), "worker_a", "result", "alpha", barrier),
    )
    p2 = multiprocessing.Process(
        target=_mp_worker,
        args=(str(prefs_file), "worker_b", "result", "beta", barrier),
    )

    p1.start()
    p2.start()
    p1.join(timeout=10)
    p2.join(timeout=10)

    assert not p1.is_alive()
    assert not p2.is_alive()
    assert p1.exitcode == 0
    assert p2.exitcode == 0

    data = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]
    assert data["worker_a"] == {"init": 1, "result": "alpha"}
    assert data["worker_b"] == {"init": 2, "result": "beta"}


# ── 9. Lock contention and event-loop responsiveness ──
#
# The preference endpoints run the whole locked read-modify-write on a worker
# thread, so waiting for the cross-process file lock must not stall the event
# loop. These tests hold the real lock file from outside the app and gate on
# events, not on elapsed time. Sleeps are only used to yield to the loop.

class _ExternalLock:
    """Hold the prefs store's flock on a separate file description."""

    def __init__(self, prefs_file, safety_timeout=3.0):
        fcntl = pytest.importorskip("fcntl")
        self._fcntl = fcntl
        self.path = os.path.abspath(str(prefs_file)) + ".lock"
        self.safety_timeout = safety_timeout
        self.held = threading.Event()
        self.release = threading.Event()
        # Set immediately before the unlock, so a write that finishes while
        # this is unset proves the lock was not honoured.
        self.released = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a+b") as handle:
            self._fcntl.flock(handle, self._fcntl.LOCK_EX)
            self.held.set()
            # The safety timeout only bounds a failing test; passing runs are
            # released explicitly by the test.
            self.release.wait(self.safety_timeout)
            self.released.set()
            self._fcntl.flock(handle, self._fcntl.LOCK_UN)

    def __enter__(self):
        self._thread.start()
        assert self.held.wait(timeout=2.0), "external lock was never acquired"
        return self

    def __exit__(self, *exc):
        self.release.set()
        self._thread.join(timeout=5.0)
        assert not self._thread.is_alive()


def _assert_store_lock_is_free(prefs_file):
    """The store lock can be taken immediately: nothing was left held."""
    fcntl = pytest.importorskip("fcntl")
    with open(os.path.abspath(str(prefs_file)) + ".lock", "a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.fixture
def async_prefs_app(tmp_path, monkeypatch):
    """App whose caller identity comes from the ``x-user`` request header."""
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "keep_alice": "kept"},
            "bob": {"theme": "paper", "keep_bob": "kept"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(
        prefs_routes, "get_current_user", lambda req: req.headers.get("x-user")
    )
    app = FastAPI()
    app.include_router(prefs_routes.setup_prefs_routes())
    return app, prefs_file


def _client(app):
    import httpx
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _ticks_while_pending(task, wanted=10):
    """Count loop turns that complete while ``task`` is still pending.

    A responsive loop reaches ``wanted`` turns while the write waits for the
    lock. A blocked loop cannot advance, so the count stays near zero.
    """
    import asyncio
    ticks = 0
    while ticks < wanted and not task.done():
        await asyncio.sleep(0.005)
        ticks += 1
    return ticks


def _users(prefs_file):
    return json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]


@pytest.mark.anyio
async def test_uncontended_write_runs_on_worker_thread(async_prefs_app, monkeypatch):
    """Test A: an uncontended write succeeds, off the event-loop thread."""
    app, prefs_file = async_prefs_app
    loop_thread = threading.get_ident()
    seen = []
    real = prefs_routes._update_for_user

    def spy(user, updates):
        seen.append(threading.get_ident())
        return real(user, updates)

    monkeypatch.setattr(prefs_routes, "_update_for_user", spy)

    async with _client(app) as ac:
        put = await ac.put("/api/prefs/quick", json={"value": 1}, headers={"x-user": "alice"})
        patch = await ac.patch(
            "/api/prefs", json={"values": {"a": 1, "b": 2}}, headers={"x-user": "alice"}
        )

    assert put.status_code == 200 and put.json() == {"key": "quick", "value": 1}
    assert patch.status_code == 200 and patch.json() == {"values": {"a": 1, "b": 2}}
    assert seen and all(ident != loop_thread for ident in seen)
    assert _users(prefs_file)["alice"] == {
        "theme": "light", "keep_alice": "kept", "quick": 1, "a": 1, "b": 2
    }


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["put", "patch"])
async def test_external_lock_contention_waits_then_persists(async_prefs_app, method):
    """Test B: a write waits for an external lock holder, then persists."""
    app, prefs_file = async_prefs_app
    before = prefs_file.read_text(encoding="utf-8")
    headers = {"x-user": "alice"}

    async with _client(app) as ac:
        with _ExternalLock(prefs_file) as lock:
            import asyncio
            if method == "put":
                request = ac.put("/api/prefs/contended", json={"value": "v"}, headers=headers)
            else:
                request = ac.patch(
                    "/api/prefs", json={"values": {"contended": "v"}}, headers=headers
                )
            write = asyncio.create_task(request)
            await _ticks_while_pending(write)
            # Still waiting, and nothing has been written under the held lock.
            assert not write.done()
            assert prefs_file.read_text(encoding="utf-8") == before
            lock.release.set()
            response = await write
        # The write could only finish after the holder unlocked.
        assert lock.released.is_set()

    assert response.status_code == 200
    assert _users(prefs_file)["alice"]["contended"] == "v"
    assert _users(prefs_file)["alice"]["keep_alice"] == "kept"


@pytest.mark.anyio
async def test_event_loop_stays_responsive_during_lock_contention(async_prefs_app):
    """Test C: unrelated async work progresses while a write awaits the lock."""
    import asyncio
    app, prefs_file = async_prefs_app

    async with _client(app) as ac:
        with _ExternalLock(prefs_file) as lock:
            write = asyncio.create_task(
                ac.put("/api/prefs/probe", json={"value": "v"}, headers={"x-user": "alice"})
            )
            ticks = await _ticks_while_pending(write)
            still_waiting = not write.done()
            lock.release.set()
            await write

    assert ticks >= 10 and still_waiting
    assert _users(prefs_file)["alice"]["probe"] == "v"


@pytest.mark.anyio
async def test_responsiveness_probe_detects_a_blocking_implementation(
    async_prefs_app, monkeypatch
):
    """Negative control for Test C: calling the transaction inline on the loop
    (the pre-offload behaviour) makes the same probe fail, so Test C is
    sensitive to the defect it guards against."""
    import asyncio
    app, prefs_file = async_prefs_app

    async def blocking(user, updates):
        return prefs_routes._update_for_user(user, updates)

    monkeypatch.setattr(prefs_routes, "_update_off_loop", blocking)

    async with _client(app) as ac:
        # Short safety timeout: with a blocked loop nothing else can release
        # the holder, so this bounds the control run.
        with _ExternalLock(prefs_file, safety_timeout=0.5):
            write = asyncio.create_task(
                ac.put("/api/prefs/probe", json={"value": "v"}, headers={"x-user": "alice"})
            )
            ticks = await _ticks_while_pending(write)
            await write

    assert ticks < 10
    assert _users(prefs_file)["alice"]["probe"] == "v"


@pytest.mark.anyio
async def test_queued_writes_from_two_users_all_persist(async_prefs_app):
    """Tests D and E: queued writers for two users, mixing PATCH and PUT on the
    same user, are serialized after the lock is released and none are lost."""
    import asyncio
    app, prefs_file = async_prefs_app

    async with _client(app) as ac:
        with _ExternalLock(prefs_file) as lock:
            tasks = [
                asyncio.create_task(ac.patch(
                    "/api/prefs", json={"values": {"a1": 1, "a2": 2}},
                    headers={"x-user": "alice"})),
                asyncio.create_task(ac.put(
                    "/api/prefs/a3", json={"value": 3}, headers={"x-user": "alice"})),
                asyncio.create_task(ac.patch(
                    "/api/prefs", json={"values": {"b1": 1, "b2": 2}},
                    headers={"x-user": "bob"})),
                asyncio.create_task(ac.put(
                    "/api/prefs/b3", json={"value": 3}, headers={"x-user": "bob"})),
            ]
            await _ticks_while_pending(tasks[0])
            assert not any(task.done() for task in tasks)
            lock.release.set()
            responses = await asyncio.gather(*tasks)

    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    users = _users(prefs_file)
    assert users["alice"] == {
        "theme": "light", "keep_alice": "kept", "a1": 1, "a2": 2, "a3": 3
    }
    assert users["bob"] == {
        "theme": "paper", "keep_bob": "kept", "b1": 1, "b2": 2, "b3": 3
    }


async def _wait_for_store_lock_free(prefs_file, timeout=5.0):
    """Poll (bounded) until the worker's transaction has unlocked the store."""
    import asyncio
    import time
    deadline = time.monotonic() + timeout
    while True:
        try:
            _assert_store_lock_is_free(prefs_file)
            return
        except BlockingIOError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_cancelled_request_still_completes_and_releases_the_lock(
    async_prefs_app, monkeypatch
):
    """Cancelling a waiting request neither strands the lock nor duplicates or
    half-applies the write.

    The awaiting request stops waiting at once, but the worker thread cannot be
    interrupted: it finishes the queued transaction exactly once and unlocks.
    """
    import asyncio
    app, prefs_file = async_prefs_app
    writes = []
    real_write = prefs_routes.atomic_write_json

    def counting_write(path, data, **kwargs):
        writes.append(path)
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(prefs_routes, "atomic_write_json", counting_write)

    async with _client(app) as ac:
        with _ExternalLock(prefs_file) as lock:
            write = asyncio.create_task(
                ac.patch("/api/prefs", json={"values": {"x": 1, "y": 2}},
                         headers={"x-user": "alice"})
            )
            await _ticks_while_pending(write)
            assert not write.done()
            write.cancel()
            with pytest.raises(asyncio.CancelledError):
                await write
            # The caller is gone, but the worker is still queued on the lock.
            assert writes == []
            lock.release.set()
        # Once the external holder is gone, the orphaned worker takes the lock,
        # writes once, and releases it.
        await _wait_for_store_lock_free(prefs_file)
        for _ in range(200):
            if writes:
                break
            await asyncio.sleep(0.01)
        await _wait_for_store_lock_free(prefs_file)

    assert len(writes) == 1
    assert _users(prefs_file)["alice"] == {
        "theme": "light", "keep_alice": "kept", "x": 1, "y": 2
    }


@pytest.mark.anyio
async def test_persistence_failure_releases_lock_and_preserves_store(
    async_prefs_app, monkeypatch
):
    """Test F: a failure inside the transaction leaves the file untouched, no
    temp debris, no held lock, and later writes still work."""
    import core.atomic_io as atomic_io
    app, prefs_file = async_prefs_app
    before = prefs_file.read_text(encoding="utf-8")
    real_dump = atomic_io.json.dump

    def failing_dump(obj, fp, **kwargs):
        raise OSError("Disk write error")

    monkeypatch.setattr(atomic_io.json, "dump", failing_dump)

    import httpx
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        failed = await ac.patch(
            "/api/prefs", json={"values": {"doomed": 1}}, headers={"x-user": "alice"}
        )
        assert failed.status_code == 500
        assert prefs_file.read_text(encoding="utf-8") == before
        assert sorted(p.name for p in prefs_file.parent.iterdir()) == [
            prefs_file.name, prefs_file.name + ".lock"
        ]
        _assert_store_lock_is_free(prefs_file)

        monkeypatch.setattr(atomic_io.json, "dump", real_dump)
        ok = await ac.put("/api/prefs/after", json={"value": "fine"}, headers={"x-user": "alice"})

    assert ok.status_code == 200
    assert _users(prefs_file)["alice"]["after"] == "fine"
    assert "doomed" not in _users(prefs_file)["alice"]


def test_blocking_lock_wait_is_visible_to_sync_client(app_and_client):
    """Sync-client view of Test B: the request returns only after the holder
    unlocks, then the value is persisted."""
    app, active_user, prefs_file = app_and_client
    active_user["user"] = "alice"
    client = TestClient(app)

    with _ExternalLock(prefs_file) as lock:
        threading.Timer(0.1, lock.release.set).start()
        resp = client.put("/api/prefs/contended_key", json={"value": "persisted_value"})
        assert lock.released.is_set()

    assert resp.status_code == 200
    data = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    assert data["contended_key"] == "persisted_value"
