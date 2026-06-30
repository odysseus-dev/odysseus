import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _import_contacts(tmp_path, monkeypatch):
    sys.modules.setdefault("core.database", MagicMock())

    monkeypatch.setattr(
        "routes.contacts_routes.SETTINGS_FILE",
        tmp_path / "settings.json",
    )
    monkeypatch.setattr(
        "routes.contacts_routes.DATA_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "routes.contacts_routes.LOCAL_CONTACTS_FILE",
        tmp_path / "contacts.json",
    )

    sys.modules.pop("src.secret_storage", None)
    from src import secret_storage
    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)

    sys.modules.pop("routes.contacts_routes", None)
    from routes import contacts_routes
    return contacts_routes


def test_carddav_password_encrypted_at_rest(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    settings = contacts._load_settings()
    password = "my-carddav-secret"
    from src.secret_storage import encrypt
    settings["carddav_password"] = encrypt(password)
    contacts._save_settings(settings)

    raw_text = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert password not in raw_text
    raw = json.loads(raw_text)
    assert raw["carddav_password"].startswith("enc:")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == password


def test_get_carddav_config_decrypts_encrypted_value(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    from src.secret_storage import encrypt
    encrypted = encrypt("super-secret")
    settings = {
        "carddav_url": "https://carddav.example",
        "carddav_username": "u",
        "carddav_password": encrypted,
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    cfg = contacts._get_carddav_config()
    assert cfg["url"] == "https://carddav.example"
    assert cfg["username"] == "u"
    assert cfg["password"] == "super-secret"


def test_get_carddav_config_plaintext_legacy_passthrough(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    settings = {
        "carddav_url": "https://carddav.example",
        "carddav_username": "u",
        "carddav_password": "legacy-plaintext",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == "legacy-plaintext"


def test_get_carddav_config_env_var_passthrough(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)
    monkeypatch.setenv("CARDDAV_PASSWORD", "env-pass")

    settings = {
        "carddav_url": "https://carddav.example",
        "carddav_username": "u",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == "env-pass"


def test_get_carddav_config_env_var_not_decrypted(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    monkeypatch.setenv("CARDDAV_PASSWORD", "env:plain-value-not-encrypted")
    settings = {
        "carddav_url": "https://carddav.example",
        "carddav_username": "u",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == "env:plain-value-not-encrypted"


def test_get_carddav_config_empty_password(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    settings = {
        "carddav_url": "https://carddav.example",
        "carddav_username": "u",
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == ""


def test_get_carddav_config_no_settings_file(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == ""
    assert cfg["url"] == ""


def test_double_save_encrypted_value_not_corrupted(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    from src.secret_storage import encrypt
    password = "persistent-secret"
    encrypted = encrypt(password)

    settings = {"carddav_password": encrypted}
    contacts._save_settings(settings)

    settings2 = contacts._load_settings()
    contacts._save_settings(settings2)

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == password


def test_double_save_re_encrypts_already_encrypted_is_noop(tmp_path, monkeypatch):
    contacts = _import_contacts(tmp_path, monkeypatch)

    from src.secret_storage import encrypt
    password = "another-secret"

    settings = contacts._load_settings()
    settings["carddav_password"] = encrypt(password)
    contacts._save_settings(settings)

    settings2 = contacts._load_settings()
    settings2["carddav_password"] = encrypt(settings2["carddav_password"])
    contacts._save_settings(settings2)

    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["carddav_password"].startswith("enc:")

    cfg = contacts._get_carddav_config()
    assert cfg["password"] == password
