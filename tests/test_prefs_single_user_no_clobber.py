"""Saving prefs with auth disabled must not wipe a multi-user store.

When auth is disabled get_current_user returns None. _save_for_user(None,...)
wrote prefs flat, overwriting the entire {"_users": {...}} map and destroying
every other user's preferences (a realistic ops transition: auth turned off
on a deployment that previously ran multi-user). It must preserve the other
users and round-trip the change into the same (first) slot _load_for_user
reads from.

Foreground fallback keys are the exception: auth-disabled consent is stored
at the flat root so it can never become consent for the first named owner.
"""
import json

import routes.prefs_routes as pr


def test_single_user_save_preserves_other_users(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"_users": {
        "alice": {"theme": "light"},
        "bob": {"theme": "paper"},
    }}), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))

    # auth disabled: load (first user) -> modify -> save
    current = pr._load_for_user(None)
    current["theme"] = "dark"
    pr._save_for_user(None, current)

    data = json.loads(f.read_text())
    assert "_users" in data, "multi-user store was clobbered"
    assert "bob" in data["_users"] and data["_users"]["bob"] == {"theme": "paper"}
    # the change round-tripped into the first user's slot
    assert data["_users"]["alice"]["theme"] == "dark"


def test_legacy_flat_store_still_saved_flat(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"theme": "light"}), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))

    pr._save_for_user(None, {"theme": "dark"})
    data = json.loads(f.read_text())
    assert data == {"theme": "dark"}


def test_named_user_save_unaffected(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"_users": {"alice": {"theme": "light"}}}), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))

    pr._save_for_user("bob", {"theme": "dark"})
    data = json.loads(f.read_text())
    assert data["_users"]["alice"] == {"theme": "light"}
    assert data["_users"]["bob"] == {"theme": "dark"}


def test_auth_disabled_fallback_consent_does_not_mutate_first_named_user(
    tmp_path,
    monkeypatch,
):
    f = tmp_path / "user_prefs.json"
    f.write_text(json.dumps({"_users": {
        "alice": {"theme": "light"},
        "bob": {"theme": "paper"},
    }}), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))

    current = pr._load_for_user(None)
    current["foreground_fallback_enabled"] = True
    current["foreground_model_fallbacks"] = [
        {"endpoint_id": "single-user", "model": "single-model"},
    ]
    pr._save_for_user(None, current)

    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["foreground_fallback_enabled"] is True
    assert data["foreground_model_fallbacks"][0]["endpoint_id"] == "single-user"
    assert data["_users"]["alice"] == {"theme": "light"}
    assert data["_users"]["bob"] == {"theme": "paper"}


def test_auth_disabled_save_preserves_named_fallback_consent(tmp_path, monkeypatch):
    f = tmp_path / "user_prefs.json"
    alice_fallbacks = [{"endpoint_id": "alice", "model": "alice-model"}]
    f.write_text(json.dumps({"_users": {
        "alice": {
            "theme": "light",
            "foreground_fallback_enabled": True,
            "foreground_model_fallbacks": alice_fallbacks,
        },
    }}), encoding="utf-8")
    monkeypatch.setattr(pr, "PREFS_FILE", str(f))

    current = pr._load_for_user(None)
    assert "foreground_fallback_enabled" not in current
    assert "foreground_model_fallbacks" not in current
    current["theme"] = "dark"
    current["foreground_fallback_enabled"] = False
    current["foreground_model_fallbacks"] = []
    pr._save_for_user(None, current)

    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["foreground_fallback_enabled"] is False
    assert data["foreground_model_fallbacks"] == []
    assert data["_users"]["alice"] == {
        "theme": "dark",
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": alice_fallbacks,
    }
