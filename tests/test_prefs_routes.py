import json

import routes.prefs_routes as prefs_routes


def test_load_ignores_non_object_prefs_file(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps(["not", "a", "prefs", "object"]), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))

    assert prefs_routes._load() == {}
    assert prefs_routes._load_for_user("alice") == {}


def test_load_keeps_object_prefs_file(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))

    assert prefs_routes._load_for_user(None) == {"theme": "dark"}
    assert prefs_routes._load_for_user("alice") == {}


def test_named_preference_write_does_not_copy_flat_fallback_consent(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "theme": "light",
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "legacy-single-user", "model": "legacy-model"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))

    bob = prefs_routes._load_for_user("bob")
    bob["theme"] = "dark"
    prefs_routes._save_for_user("bob", bob)

    raw = prefs_routes._load()
    assert raw["_users"] == {"bob": {"theme": "dark"}}
    assert raw["foreground_fallback_enabled"] is True
    assert raw["foreground_model_fallbacks"][0]["endpoint_id"] == "legacy-single-user"
