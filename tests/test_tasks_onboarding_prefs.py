"""Tasks onboarding must update only its own preference keys, atomically.

The onboarding endpoint used to read the caller's whole preference record,
then write the whole record back. A Settings save that landed in between was
silently reverted. These tests drive the real endpoint against the real
preference store and gate the interleaving on events, not on elapsed time.
"""

import asyncio
import json
import os
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.prefs_routes as prefs_routes
import routes.task_routes as task_routes
from tests.test_prefs_concurrency import _ExternalLock, _ticks_while_pending


@pytest.fixture
def onboarding_env(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "keep_alice": "kept"},
            "bob": {"theme": "paper", "tasks_opened": False, "keep_bob": "kept"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))

    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        task_routes,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, autocommit=False),
    )

    scheduler = MagicMock()
    scheduler.ensure_defaults = AsyncMock()
    router = task_routes.setup_task_routes(scheduler)
    post = next(
        route.endpoint
        for route in router.routes
        if route.path.endswith("/onboarding") and "POST" in route.methods
    )
    return SimpleNamespace(post=post, prefs_file=prefs_file, scheduler=scheduler)


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(current_user=user), headers={})


def _users(prefs_file):
    return json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]


def _lock_is_held(prefs_file):
    """True when another file description holds the store's flock."""
    fcntl = pytest.importorskip("fcntl")
    with open(os.path.abspath(str(prefs_file)) + ".lock", "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


@pytest.mark.anyio
async def test_settings_save_between_onboarding_read_and_write_is_kept(
    onboarding_env, monkeypatch
):
    """The interleaving behind the lost update, forced by events.

    1. Onboarding reads the preference record.
    2. Settings changes an unrelated preference and finishes.
    3. Onboarding writes its own keys.

    If onboarding's read is not protected by the store lock, step 2 completes
    before step 3 and the stale snapshot erases it. If the read is protected,
    Settings cannot finish until onboarding is done; either way both survive.
    """
    env = onboarding_env
    onboarding_read = threading.Event()
    settings_done = threading.Event()
    settings_error = []

    def settings_save():
        try:
            onboarding_read.wait(timeout=5.0)
            prefs_routes._update_for_user("alice", {"theme": "dark", "added": 1})
        except BaseException as exc:  # surfaced by the assertion below
            settings_error.append(exc)
        finally:
            settings_done.set()

    settings = threading.Thread(target=settings_save, daemon=True)
    settings.start()

    real_load = prefs_routes._load
    fired = []

    def gated_load():
        data = real_load()
        if not fired:  # Settings starts only after this, so this is onboarding
            fired.append(True)
            onboarding_read.set()
            if not _lock_is_held(env.prefs_file):
                # The read is unprotected: let Settings complete before the
                # onboarding write, which is exactly the lost-update window.
                assert settings_done.wait(timeout=5.0)
        return data

    monkeypatch.setattr(prefs_routes, "_load", gated_load)
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    result = await env.post(_req("alice"), {"enabled": True})
    settings.join(timeout=5.0)

    assert fired, "onboarding never read the preference store"
    assert not settings_error, settings_error
    assert result["ok"] is True and result["enabled"] is True
    assert _users(env.prefs_file)["alice"] == {
        "theme": "dark",
        "added": 1,
        "keep_alice": "kept",
        "tasks_opened": True,
        "tasks_enabled": True,
    }


@pytest.mark.anyio
async def test_onboarding_writes_only_its_keys_for_the_caller(onboarding_env, monkeypatch):
    env = onboarding_env
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")
    bob_before = _users(env.prefs_file)["bob"]

    result = await env.post(_req("alice"), {})

    assert result == {"ok": True, "opened": True, "enabled": False, "resumed": 0}
    users = _users(env.prefs_file)
    assert users["alice"] == {"theme": "light", "keep_alice": "kept", "tasks_opened": True}
    assert users["bob"] == bob_before
    env.scheduler.ensure_defaults.assert_awaited_once_with("alice")


@pytest.mark.anyio
async def test_onboarding_never_clears_an_existing_enabled_flag(onboarding_env, monkeypatch):
    env = onboarding_env
    prefs_routes._update_for_user("alice", {"tasks_enabled": True})
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    result = await env.post(_req("alice"), {"enabled": False})

    assert result["enabled"] is True
    assert _users(env.prefs_file)["alice"]["tasks_enabled"] is True


@pytest.mark.anyio
async def test_onboarding_recreates_values_cleared_by_settings(onboarding_env, monkeypatch):
    env = onboarding_env
    prefs_routes._update_for_user("alice", {"tasks_opened": True, "tasks_enabled": True})
    prefs_routes._update_for_user("alice", {"tasks_opened": None, "tasks_enabled": None})
    assert "tasks_opened" not in _users(env.prefs_file)["alice"]
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    result = await env.post(_req("alice"), {"enabled": False})

    assert result["enabled"] is False
    alice = _users(env.prefs_file)["alice"]
    assert alice["tasks_opened"] is True
    assert "tasks_enabled" not in alice
    assert alice["theme"] == "light" and alice["keep_alice"] == "kept"


@pytest.mark.anyio
async def test_onboarding_is_isolated_between_users(onboarding_env, monkeypatch):
    env = onboarding_env
    current = {}
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: current["user"])

    current["user"] = "alice"
    first = await env.post(_req("alice"), {"enabled": True})
    current["user"] = "bob"
    second = await env.post(_req("bob"), {"enabled": False})
    assert first["enabled"] is True and second["enabled"] is False

    users = _users(env.prefs_file)
    assert users["alice"]["tasks_enabled"] is True
    assert "tasks_enabled" not in users["bob"]
    assert users["bob"]["tasks_opened"] is True and users["bob"]["keep_bob"] == "kept"
    assert users["alice"]["keep_alice"] == "kept"


@pytest.mark.anyio
async def test_concurrent_onboarding_and_settings_writes_all_persist(
    onboarding_env, monkeypatch
):
    env = onboarding_env
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")

    updates = [
        asyncio.create_task(
            prefs_routes._update_off_loop("alice", {f"setting_{index}": index})
        )
        for index in range(12)
    ]
    onboarding = asyncio.create_task(env.post(_req("alice"), {"enabled": True}))
    await asyncio.gather(*updates, onboarding)

    alice = _users(env.prefs_file)["alice"]
    assert {key: alice[key] for key in (f"setting_{i}" for i in range(12))} == {
        f"setting_{i}": i for i in range(12)
    }
    assert alice["tasks_opened"] is True and alice["tasks_enabled"] is True
    assert alice["theme"] == "light" and alice["keep_alice"] == "kept"


@pytest.mark.anyio
async def test_onboarding_waits_for_the_store_lock_without_blocking_the_loop(onboarding_env, monkeypatch):
    env = onboarding_env
    monkeypatch.setattr(task_routes, "get_current_user", lambda request: "alice")
    before = env.prefs_file.read_text(encoding="utf-8")

    with _ExternalLock(env.prefs_file) as lock:
        write = asyncio.create_task(env.post(_req("alice"), {"enabled": True}))
        ticks = await _ticks_while_pending(write)
        still_waiting = not write.done()
        unchanged = env.prefs_file.read_text(encoding="utf-8") == before
        lock.release.set()
        result = await write
    assert lock.released.is_set()

    assert ticks >= 10 and still_waiting and unchanged
    assert result["enabled"] is True
    assert _users(env.prefs_file)["alice"]["tasks_enabled"] is True
