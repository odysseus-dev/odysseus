"""Saving prefs with auth disabled must not wipe a multi-user store.

When auth is disabled get_current_user returns None. _save_for_user(None,...)
wrote prefs flat, overwriting the entire {"_users": {...}} map and destroying
every other user's preferences (a realistic ops transition: auth turned off
on a deployment that previously ran multi-user). It must preserve the other
users and round-trip the change into the same (first) slot _load_for_user
reads from.
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
