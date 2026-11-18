"""CalDAV account changes must not revert unrelated preference saves.

Accounts live in the caller's preference record. Saving them used to read the
whole record and write it back unlocked (and on the event loop), and the
legacy-account migration did the same, so a Settings save in between was lost.
These tests use the real routes and the real preference store.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import routes.calendar_routes as cr
import routes.prefs_routes as prefs_routes
from src import caldav_sync
from tests.helpers.prefs_race import SettingsSaveRace
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
    monkeypatch.setattr(cr, "_require_user", lambda request: "alice")
    router = cr.setup_calendar_routes()

    def endpoint(method, path):
        return next(
            route.endpoint for route in router.routes
            if route.path == f"/api/calendar{path}" and method in route.methods
        )

    return SimpleNamespace(endpoint=endpoint, prefs_file=prefs_file)


def _users(prefs_file):
    return json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]


def _request(body=None):
    async def read_json():
        return body or {}
    return SimpleNamespace(json=read_json, headers={}, state=SimpleNamespace(current_user="alice"))


@pytest.mark.anyio
async def test_settings_save_during_an_account_change_is_kept(calendar_env, monkeypatch):
    env = calendar_env
    # The handler reads once to find the account and again to save. The second
    # read is the one that precedes the write.
    race = SettingsSaveRace(
        prefs_routes, env.prefs_file, monkeypatch,
        user="alice", updates={"theme": "dark", "added_in_settings": 1}, after_read=2,
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


@pytest.mark.anyio
async def test_a_failed_write_leaves_accounts_intact_and_the_store_unlocked(calendar_env, monkeypatch):
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
async def test_an_account_change_waits_for_the_lock_without_blocking_the_loop(calendar_env):
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
