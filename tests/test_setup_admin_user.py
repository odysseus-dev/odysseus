import importlib.util
import json
import sqlite3
from pathlib import Path


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("odysseus_setup_under_test", Path("setup.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_create_default_admin_normalizes_env_username(tmp_path, monkeypatch):
    setup_module = _load_setup_module()
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(tmp_path / "auth.json"))
    monkeypatch.setenv("ODYSSEUS_ADMIN_USER", " AdminUser ")
    monkeypatch.setenv("ODYSSEUS_ADMIN_PASSWORD", "temporary-password")

    assert setup_module.create_default_admin() == "created"

    auth_path = tmp_path / "auth.json"
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert "adminuser" in data["users"]
    assert "AdminUser" not in data["users"]


def test_create_default_admin_can_defer_to_web_setup(tmp_path, monkeypatch):
    setup_module = _load_setup_module()
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(auth_path))
    monkeypatch.setenv("ODYSSEUS_DEFER_ADMIN_SETUP", "1")

    assert setup_module.create_default_admin() == "deferred"
    assert not auth_path.exists()


def test_deferred_setup_backs_up_minimal_generated_admin(tmp_path, monkeypatch):
    setup_module = _load_setup_module()
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "users": {
            "admin": {
                "password_hash": "$2b$12$placeholder",
                "is_admin": True,
            }
        }
    }), encoding="utf-8")
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.execute("CREATE TABLE chat_messages (id TEXT)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(setup_module, "AUTH_FILE", str(auth_path))
    monkeypatch.setenv("ODYSSEUS_DEFER_ADMIN_SETUP", "1")

    assert setup_module.create_default_admin() == "deferred"
    assert not auth_path.exists()
    backups = list(tmp_path.glob("auth.json.deferred-setup-backup-*"))
    assert len(backups) == 1
