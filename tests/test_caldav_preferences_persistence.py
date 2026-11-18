"""Deterministic tests for CalDAV preferences persistence, concurrency, and serialization.

Validates:
- Test A: Two concurrent adds for the same owner
- Test B: Concurrent updates to different accounts
- Test C: Concurrent add and delete
- Test D: Concurrent update and delete serialization (404 and ordering)
- Test E: Legacy migration race under both lock acquisition orders
- Test F: Concurrent Settings preference write preservation
- Test G: Isolated concurrency across different users
- Test H: Persistence failure handling (no corruption, no orphaned lock, propagation)
- Test I: Event-loop responsiveness during lock contention
"""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.calendar_routes as cr
import routes.prefs_routes as prefs_routes
from src import caldav_sync
from tests.helpers.prefs_race import SettingsSaveRace, store_lock_is_held
from tests.test_prefs_concurrency import _ExternalLock, _assert_store_lock_is_free, _ticks_while_pending

ACCOUNTS = [
    {"id": "acc-1", "label": "Home", "url": "https://cal.example.com/a", "username": "a", "password": "enc:1"},
    {"id": "acc-2", "label": "Work", "url": "https://cal.example.com/b", "username": "b", "password": "enc:2"},
]


@pytest.fixture
def calendar_env(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "keep_alice": "kept", "caldav_accounts": ACCOUNTS},
            "bob": {"theme": "paper", "caldav_accounts": [ACCOUNTS[0]]},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(cr, "_require_user", lambda request: getattr(getattr(request, "state", None), "current_user", "alice"))
    monkeypatch.setattr(caldav_sync, "validate_caldav_url", lambda u: u.strip())
    router = cr.setup_calendar_routes()

    def endpoint(method, path):
        return next(
            route.endpoint for route in router.routes
            if route.path == f"/api/calendar{path}" and method in route.methods
        )

    return SimpleNamespace(endpoint=endpoint, prefs_file=prefs_file)


def _users(prefs_file):
    return json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]


def _request(body=None, user="alice"):
    async def read_json():
        return body or {}
    return SimpleNamespace(json=read_json, headers={}, state=SimpleNamespace(current_user=user))


class CalDAVAccountRace:
    """Forces concurrent action ``race_action`` to complete after the target handler's
    first read IF that read was unprotected by the store lock.
    """
    def __init__(self, prefs_routes_mod, prefs_file, monkeypatch, *, race_action, after_read=1):
        self._prefs_file = prefs_file
        self._race_action = race_action
        self._after_read = after_read
        self._reads = 0
        self._read_done = threading.Event()
        self._action_done = threading.Event()
        self.errors = []
        self.fired = False
        real_load = prefs_routes_mod._load

        def gated_load():
            data = real_load()
            self._reads += 1
            if self._reads == self._after_read:
                self.fired = True
                self._read_done.set()
                if not store_lock_is_held(self._prefs_file):
                    assert self._action_done.wait(timeout=5.0)
            return data

        monkeypatch.setattr(prefs_routes_mod, "_load", gated_load)
        self._thread = threading.Thread(target=self._run_action, daemon=True)
        self._thread.start()

    def _run_action(self):
        try:
            self._read_done.wait(timeout=5.0)
            self._race_action()
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self._action_done.set()

    def finish(self):
        self._thread.join(timeout=5.0)
        assert self.fired, "the handler never reached the targeted preference read"
        assert not self.errors, self.errors


# ── Test A: Two concurrent adds ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_two_concurrent_adds_preserve_both_accounts(calendar_env, monkeypatch):
    """Test A (mandatory): Two concurrent adds for the same owner must preserve both accounts.

    Under the old implementation, both requests read the account list outside
    the transaction, so the later writer would overwrite the earlier one.
    With the complete read-modify-write serialized inside the transaction,
    both accounts are safely appended and persisted.
    """
    env = calendar_env
    res_b = []

    def add_second():
        r = asyncio.run(env.endpoint("POST", "/config/accounts")(
            _request({
                "url": "https://cal.example.com/b-new",
                "username": "user_b",
                "password": "pw_b",
                "label": "Account B",
            })
        ))
        res_b.append(r)

    race = CalDAVAccountRace(
        prefs_routes, env.prefs_file, monkeypatch,
        race_action=add_second, after_read=1,
    )

    result_a = await env.endpoint("POST", "/config/accounts")(
        _request({
            "url": "https://cal.example.com/a-new",
            "username": "user_a",
            "password": "pw_a",
            "label": "Account A",
        })
    )
    race.finish()

    assert result_a["ok"] is True
    assert len(res_b) == 1 and res_b[0]["ok"] is True
    id_a = result_a["id"]
    id_b = res_b[0]["id"]
    assert id_a != id_b

    alice = _users(env.prefs_file)["alice"]
    account_ids = [a["id"] for a in alice["caldav_accounts"]]
    # Both new accounts must exist
    assert id_a in account_ids
    assert id_b in account_ids
    # Existing accounts must not disappear
    assert "acc-1" in account_ids
    assert "acc-2" in account_ids
    assert len(account_ids) == 4
    # Unrelated preference keys remain unchanged
    assert alice["theme"] == "light"
    assert alice["keep_alice"] == "kept"


# ── Test B: Concurrent updates to different accounts ──────────────────────────

@pytest.mark.anyio
async def test_concurrent_updates_to_different_accounts(calendar_env, monkeypatch):
    """Test B: Concurrent updates to different accounts belonging to the same owner."""
    env = calendar_env
    res_2 = []

    def update_second():
        r = asyncio.run(env.endpoint("PUT", "/config/accounts/{account_id}")(
            "acc-2", _request({"label": "Updated Work", "username": "b_updated"})
        ))
        res_2.append(r)

    race = CalDAVAccountRace(
        prefs_routes, env.prefs_file, monkeypatch,
        race_action=update_second, after_read=1,
    )

    result_1 = await env.endpoint("PUT", "/config/accounts/{account_id}")(
        "acc-1", _request({"label": "Updated Home", "username": "a_updated"})
    )
    race.finish()

    assert result_1 == {"ok": True}
    assert res_2 == [{"ok": True}]

    alice = _users(env.prefs_file)["alice"]
    accounts_by_id = {a["id"]: a for a in alice["caldav_accounts"]}
    assert accounts_by_id["acc-1"]["label"] == "Updated Home"
    assert accounts_by_id["acc-1"]["username"] == "a_updated"
    assert accounts_by_id["acc-2"]["label"] == "Updated Work"
    assert accounts_by_id["acc-2"]["username"] == "b_updated"
    assert alice["theme"] == "light"
    assert alice["keep_alice"] == "kept"


# ── Test C: Concurrent add and delete ──────────────────────────────────────────

@pytest.mark.anyio
async def test_concurrent_add_and_delete(calendar_env, monkeypatch):
    """Test C: Concurrent add and delete overlapping."""
    env = calendar_env
    res_del = []

    def do_delete():
        r = asyncio.run(env.endpoint("DELETE", "/config/accounts/{account_id}")(
            "acc-1", _request()
        ))
        res_del.append(r)

    race = CalDAVAccountRace(
        prefs_routes, env.prefs_file, monkeypatch,
        race_action=do_delete, after_read=1,
    )

    result_add = await env.endpoint("POST", "/config/accounts")(
        _request({
            "url": "https://cal.example.com/c",
            "username": "user_c",
            "password": "pw_c",
            "label": "Account C",
        })
    )
    race.finish()

    assert result_add["ok"] is True
    assert res_del == [{"ok": True}]

    alice = _users(env.prefs_file)["alice"]
    account_ids = [a["id"] for a in alice["caldav_accounts"]]
    assert "acc-1" not in account_ids
    assert "acc-2" in account_ids
    assert result_add["id"] in account_ids
    assert alice["theme"] == "light"


# ── Test D: Concurrent update and delete serialization ─────────────────────────

@pytest.mark.anyio
async def test_concurrent_update_and_delete_serialization(calendar_env):
    """Test D: Serialization of update and delete on the same account."""
    env = calendar_env

    # D1: Deletion completes first -> subsequent update receives 404
    del_res = await env.endpoint("DELETE", "/config/accounts/{account_id}")("acc-1", _request())
    assert del_res == {"ok": True}

    with pytest.raises(HTTPException) as exc_info:
        await env.endpoint("PUT", "/config/accounts/{account_id}")(
            "acc-1", _request({"label": "Ghost Update"})
        )
    assert exc_info.value.status_code == 404

    alice = _users(env.prefs_file)["alice"]
    assert [a["id"] for a in alice["caldav_accounts"]] == ["acc-2"]

    # D2: Update completes first -> subsequent delete removes the updated account
    up_res = await env.endpoint("PUT", "/config/accounts/{account_id}")(
        "acc-2", _request({"label": "Updated Before Delete"})
    )
    assert up_res == {"ok": True}

    del_res2 = await env.endpoint("DELETE", "/config/accounts/{account_id}")("acc-2", _request())
    assert del_res2 == {"ok": True}

    alice = _users(env.prefs_file)["alice"]
    assert alice["caldav_accounts"] == []


# ── Test E: Legacy migration race ─────────────────────────────────────────────

def test_legacy_migration_race_add_before_migration(calendar_env):
    """Test E1: Account addition acquires lock first; legacy migration re-reads and preserves."""
    env = calendar_env
    prefs_routes._update_for_user("alice", {
        "caldav_accounts": None,
        "caldav": {"url": "https://legacy.example.com", "username": "old", "password": "enc:old"},
    })

    # Add account executes
    add_res = asyncio.run(env.endpoint("POST", "/config/accounts")(
        _request({
            "url": "https://cal.example.com/new",
            "username": "new_user",
            "password": "new_password",
            "label": "New CalDAV",
        })
    ))
    assert add_res["ok"] is True

    # Legacy migration is triggered afterwards
    accounts = caldav_sync._load_caldav_accounts("alice")
    assert {a.get("url") for a in accounts} == {
        "https://legacy.example.com",
        "https://cal.example.com/new",
    }

    alice = _users(env.prefs_file)["alice"]
    assert "caldav" not in alice
    assert len(alice["caldav_accounts"]) == 2


def test_legacy_migration_race_migration_before_add(calendar_env):
    """Test E2: Migration completes first; subsequent account addition appends."""
    env = calendar_env
    prefs_routes._update_for_user("alice", {
        "caldav_accounts": None,
        "caldav": {"url": "https://legacy.example.com", "username": "old", "password": "enc:old"},
    })

    # Migration runs first
    migrated = caldav_sync._load_caldav_accounts("alice")
    assert len(migrated) == 1
    assert migrated[0]["url"] == "https://legacy.example.com"

    # Add account runs second
    add_res = asyncio.run(env.endpoint("POST", "/config/accounts")(
        _request({
            "url": "https://cal.example.com/new2",
            "username": "new_user2",
            "password": "new_password2",
        })
    ))
    assert add_res["ok"] is True

    alice = _users(env.prefs_file)["alice"]
    assert "caldav" not in alice
    assert {a.get("url") for a in alice["caldav_accounts"]} == {
        "https://legacy.example.com",
        "https://cal.example.com/new2",
    }


def test_legacy_migration_rechecks_inside_transaction(calendar_env, monkeypatch):
    """Test E3: Migration checks outside lock, but concurrent write established caldav_accounts.
    Inside the transaction, migration rechecks and preserves authoritative list.
    """
    env = calendar_env
    prefs_routes._update_for_user("alice", {
        "caldav_accounts": None,
        "caldav": {"url": "https://legacy.example.com", "username": "old", "password": "enc:old"},
    })

    def concurrent_add():
        asyncio.run(env.endpoint("POST", "/config/accounts")(
            _request({
                "url": "https://cal.example.com/interleaved",
                "username": "interleaved_user",
                "password": "pw",
            })
        ))

    race = CalDAVAccountRace(
        prefs_routes, env.prefs_file, monkeypatch,
        race_action=concurrent_add, after_read=1,
    )

    accounts = caldav_sync._load_caldav_accounts("alice")
    race.finish()

    alice = _users(env.prefs_file)["alice"]
    assert {a.get("url") for a in alice["caldav_accounts"]} == {
        "https://legacy.example.com",
        "https://cal.example.com/interleaved",
    }
    assert "caldav" not in alice


# ── Test F: Settings preference write preservation ────────────────────────────

@pytest.mark.anyio
async def test_settings_save_during_an_account_change_is_kept(calendar_env, monkeypatch):
    env = calendar_env
    # The handler reads and modifies in one locked transaction.
    race = SettingsSaveRace(
        prefs_routes, env.prefs_file, monkeypatch,
        user="alice", updates={"theme": "dark", "added_in_settings": 1}, after_read=1,
    )

    result = await env.endpoint("DELETE", "/config/accounts/{account_id}")("acc-1", _request())
    race.finish()

    assert result == {"ok": True}
    alice = _users(env.prefs_file)["alice"]
    assert [account["id"] for account in alice["caldav_accounts"]] == ["acc-2"]
    assert alice["theme"] == "dark" and alice["added_in_settings"] == 1
    assert alice["keep_alice"] == "kept"


def test_settings_save_during_legacy_account_migration_is_kept(calendar_env, monkeypatch):
    env = calendar_env
    prefs_routes._update_for_user("alice", {
        "caldav_accounts": None,
        "caldav": {"url": "https://legacy.example.com/dav", "username": "old", "password": "enc:old"},
    })
    race = SettingsSaveRace(
        prefs_routes, env.prefs_file, monkeypatch,
        user="alice", updates={"theme": "dark", "added_in_settings": 1},
    )

    accounts = caldav_sync._load_caldav_accounts("alice")
    race.finish()

    assert [account["url"] for account in accounts] == ["https://legacy.example.com/dav"]
    alice = _users(env.prefs_file)["alice"]
    assert alice["caldav_accounts"] == accounts and "caldav" not in alice
    assert alice["theme"] == "dark" and alice["added_in_settings"] == 1
    assert alice["keep_alice"] == "kept"


@pytest.mark.anyio
async def test_clearing_the_legacy_config_keeps_other_preferences_and_users(calendar_env):
    env = calendar_env
    bob_before = _users(env.prefs_file)["bob"]
    prefs_routes._update_for_user("alice", {"caldav": {"url": "https://legacy.example.com"}})

    result = await env.endpoint("POST", "/config")(_request({"url": ""}))

    assert result == {"ok": True, "cleared": True}
    users = _users(env.prefs_file)
    assert users["alice"]["caldav_accounts"] == []
    assert "caldav" not in users["alice"]
    assert users["alice"]["theme"] == "light" and users["alice"]["keep_alice"] == "kept"
    assert users["bob"] == bob_before


# ── Test G: Different users ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_concurrent_caldav_operations_different_users(calendar_env):
    """Test G: Concurrent CalDAV operations for different owners remain isolated."""
    env = calendar_env

    t_alice = asyncio.create_task(
        env.endpoint("POST", "/config/accounts")(
            _request({
                "url": "https://cal.example.com/alice-3",
                "username": "alice3",
                "password": "pw3",
            }, user="alice")
        )
    )
    t_bob = asyncio.create_task(
        env.endpoint("POST", "/config/accounts")(
            _request({
                "url": "https://cal.example.com/bob-2",
                "username": "bob2",
                "password": "pw2",
            }, user="bob")
        )
    )

    r_alice, r_bob = await asyncio.gather(t_alice, t_bob)
    assert r_alice["ok"] is True
    assert r_bob["ok"] is True

    users = _users(env.prefs_file)
    alice_accounts = users["alice"]["caldav_accounts"]
    bob_accounts = users["bob"]["caldav_accounts"]

    assert r_alice["id"] in [a["id"] for a in alice_accounts]
    assert r_alice["id"] not in [a["id"] for a in bob_accounts]

    assert r_bob["id"] in [a["id"] for a in bob_accounts]
    assert r_bob["id"] not in [a["id"] for a in alice_accounts]

    assert users["alice"]["theme"] == "light"
    assert users["bob"]["theme"] == "paper"


# ── Test H: Failure handling ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_a_failed_write_leaves_accounts_intact_and_the_store_unlocked(calendar_env, monkeypatch):
    """Test H (basic): Simulated write failure leaves store unlocked and intact."""
    env = calendar_env
    before = env.prefs_file.read_text(encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(prefs_routes, "atomic_write_json", fail)
    with pytest.raises(OSError):
        await env.endpoint("DELETE", "/config/accounts/{account_id}")("acc-1", _request())

    assert env.prefs_file.read_text(encoding="utf-8") == before
    _assert_store_lock_is_free(env.prefs_file)


@pytest.mark.anyio
async def test_persistence_failure_handling(calendar_env, monkeypatch):
    """Test H (comprehensive): Simulated persistence failure ensures no partial corruption,
    no orphaned lock, no unrelated preference loss, and correct error propagation.
    """
    env = calendar_env
    before_text = env.prefs_file.read_text(encoding="utf-8")

    real_write = prefs_routes.atomic_write_json

    def failing_write(*args, **kwargs):
        raise OSError("Disk write failed: simulated ENOSPC")

    monkeypatch.setattr(prefs_routes, "atomic_write_json", failing_write)

    # 1. Error propagation
    with pytest.raises(OSError) as exc_info:
        await env.endpoint("POST", "/config/accounts")(
            _request({
                "url": "https://cal.example.com/fail",
                "username": "fail_user",
                "password": "fail_password",
            })
        )
    assert "Disk write failed" in str(exc_info.value)

    # 2. No partial JSON corruption & no unrelated preference loss
    assert env.prefs_file.read_text(encoding="utf-8") == before_text

    # 3. No orphaned lock
    _assert_store_lock_is_free(env.prefs_file)

    # 4. Subsequent valid write succeeds after recovery
    monkeypatch.setattr(prefs_routes, "atomic_write_json", real_write)
    recovered = await env.endpoint("POST", "/config/accounts")(
        _request({
            "url": "https://cal.example.com/success",
            "username": "ok_user",
            "password": "ok_password",
        })
    )
    assert recovered["ok"] is True
    alice = _users(env.prefs_file)["alice"]
    assert recovered["id"] in [a["id"] for a in alice["caldav_accounts"]]
    assert alice["theme"] == "light"
    assert alice["keep_alice"] == "kept"


# ── Test I: Event-loop responsiveness ─────────────────────────────────────────

@pytest.mark.anyio
async def test_an_account_change_waits_for_the_lock_without_blocking_the_loop(calendar_env):
    """Test I: Event-loop responsiveness while waiting for an external lock."""
    env = calendar_env
    before = env.prefs_file.read_text(encoding="utf-8")

    with _ExternalLock(env.prefs_file) as lock:
        change = asyncio.create_task(
            env.endpoint("DELETE", "/config/accounts/{account_id}")("acc-1", _request())
        )
        ticks = await _ticks_while_pending(change)
        still_waiting = not change.done()
        unchanged = env.prefs_file.read_text(encoding="utf-8") == before
        lock.release.set()
        result = await change
    assert lock.released.is_set()

    assert ticks >= 10 and still_waiting and unchanged
    assert result == {"ok": True}
    assert [a["id"] for a in _users(env.prefs_file)["alice"]["caldav_accounts"]] == ["acc-2"]
