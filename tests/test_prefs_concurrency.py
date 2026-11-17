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
