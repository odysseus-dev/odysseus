"""Restoring a backup must replace only the preferences the backup contains.

The import used to read the account's whole preference record, merge the
backup into that copy, and write the whole record back. A Settings save that
landed in between was silently reverted. These tests drive the real endpoint
against the real preference store.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

import routes.backup_routes as br
import routes.prefs_routes as prefs_routes
from tests.helpers.prefs_race import SettingsSaveRace
from tests.test_prefs_concurrency import _ExternalLock, _assert_store_lock_is_free, _ticks_while_pending


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {"theme": "light", "language": "en", "keep_alice": "kept"},
            "bob": {"theme": "paper", "language": "fr", "keep_bob": "kept"},
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")
    router = br.setup_backup_routes(MagicMock(), MagicMock(), MagicMock())
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/import" and "POST" in route.methods
    )
    return endpoint, prefs_file


def _users(prefs_file):
    return json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]


@pytest.mark.anyio
async def test_settings_save_during_a_preference_restore_is_kept(backup_env, monkeypatch):
    endpoint, prefs_file = backup_env
    race = SettingsSaveRace(
        prefs_routes, prefs_file, monkeypatch,
        user="alice", updates={"theme": "dark", "added_in_settings": 1},
    )

    result = await endpoint(_Req({"preferences": {"language": "pt", "from_backup": True}}))
    race.finish()

    assert result["ok"] is True and result["imported"] == ["preferences"]
    assert _users(prefs_file)["alice"] == {
        "theme": "dark",            # the concurrent Settings save survives
        "added_in_settings": 1,
        "language": "pt",           # restored from the backup
        "from_backup": True,
        "keep_alice": "kept",
    }


@pytest.mark.anyio
async def test_restored_keys_replace_current_values_only_for_the_caller(backup_env):
    endpoint, prefs_file = backup_env
    bob_before = _users(prefs_file)["bob"]

    await endpoint(_Req({"preferences": {"theme": "solar", "language": "pt"}}))

    users = _users(prefs_file)
    assert users["alice"] == {"theme": "solar", "language": "pt", "keep_alice": "kept"}
    assert users["bob"] == bob_before


@pytest.mark.anyio
async def test_a_null_in_the_backup_removes_the_preference(backup_env):
    endpoint, prefs_file = backup_env

    await endpoint(_Req({"preferences": {"language": None}}))

    assert _users(prefs_file)["alice"] == {"theme": "light", "keep_alice": "kept"}


@pytest.mark.anyio
@pytest.mark.parametrize("preferences", [[], "theme", 7, None])
async def test_a_malformed_preferences_section_changes_nothing(backup_env, preferences):
    endpoint, prefs_file = backup_env
    before = prefs_file.read_text(encoding="utf-8")

    result = await endpoint(_Req({"preferences": preferences}))

    assert result["ok"] is False
    assert prefs_file.read_text(encoding="utf-8") == before


@pytest.mark.anyio
async def test_an_empty_preferences_section_leaves_the_record_as_it_was(backup_env):
    endpoint, prefs_file = backup_env
    before = _users(prefs_file)

    result = await endpoint(_Req({"preferences": {}}))

    assert result["imported"] == ["preferences"]
    assert _users(prefs_file) == before


@pytest.mark.anyio
async def test_a_failed_write_leaves_the_store_intact_and_unlocked(backup_env, monkeypatch):
    endpoint, prefs_file = backup_env
    before = prefs_file.read_text(encoding="utf-8")

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(prefs_routes, "atomic_write_json", fail)
    with pytest.raises(OSError):
        await endpoint(_Req({"preferences": {"language": "pt"}}))

    assert prefs_file.read_text(encoding="utf-8") == before
    _assert_store_lock_is_free(prefs_file)


@pytest.mark.anyio
async def test_a_preference_restore_waits_for_the_lock_without_blocking_the_loop(backup_env):
    endpoint, prefs_file = backup_env
    before = prefs_file.read_text(encoding="utf-8")

    with _ExternalLock(prefs_file) as lock:
        restore = asyncio.create_task(endpoint(_Req({"preferences": {"language": "pt"}})))
        ticks = await _ticks_while_pending(restore)
        still_waiting = not restore.done()
        unchanged = prefs_file.read_text(encoding="utf-8") == before
        lock.release.set()
        result = await restore
    assert lock.released.is_set()

    assert ticks >= 10 and still_waiting and unchanged
    assert result["ok"] is True
    assert _users(prefs_file)["alice"]["language"] == "pt"
