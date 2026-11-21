import importlib.util
import json
import os
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


def test_main_loads_admin_password_from_env_file(tmp_path, monkeypatch):
    """Regression: setup.py must honor an admin password pre-seeded in .env on
    native installs, even when the var is not exported into the shell
    (website/setup.md documents this). Previously setup.py never called
    load_dotenv(), so os.getenv() saw nothing and a random password was
    generated instead."""
    import bcrypt

    setup_module = _load_setup_module()

    # Credentials live ONLY in a .env beside setup.py (written with a UTF-8 BOM,
    # the Notepad-on-Windows case that utf-8-sig must tolerate) — not exported.
    monkeypatch.delenv("ODYSSEUS_ADMIN_USER", raising=False)
    monkeypatch.delenv("ODYSSEUS_ADMIN_PASSWORD", raising=False)
    (tmp_path / ".env").write_text(
        "ODYSSEUS_ADMIN_USER=presetuser\nODYSSEUS_ADMIN_PASSWORD=fromenvfile12345\n",
        encoding="utf-8-sig",
    )

    # Point setup at the temp dir and neutralize main()'s heavy steps.
    monkeypatch.setattr(setup_module, "BASE_DIR", str(tmp_path))
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(auth_path))
    monkeypatch.setattr(setup_module, "check_arch", lambda: None)
    monkeypatch.setattr(setup_module, "create_dirs", lambda: None)
    monkeypatch.setattr(setup_module, "create_env", lambda: None)
    monkeypatch.setattr(setup_module, "check_deps", lambda: None)
    monkeypatch.setattr(setup_module, "init_database", lambda: None)
    # Force the non-interactive branch so the test never blocks on a prompt.
    monkeypatch.setenv("ODYSSEUS_SKIP_ADMIN_PROMPT", "1")

    try:
        setup_module.main()
    finally:
        # load_dotenv writes real os.environ entries; undo so sibling tests
        # don't inherit them.
        os.environ.pop("ODYSSEUS_ADMIN_USER", None)
        os.environ.pop("ODYSSEUS_ADMIN_PASSWORD", None)

    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert "presetuser" in data["users"], data
    assert bcrypt.checkpw(
        b"fromenvfile12345", data["users"]["presetuser"]["password_hash"].encode()
    ), "admin password from .env was ignored; a random one was generated"


class _TtyStdin:
    def isatty(self):
        return True


def _generated_password(output):
    for line in output.splitlines():
        if "Temporary password:" in line:
            return line.split("Temporary password:", 1)[1].strip()
    return None


def test_skip_prompt_in_terminal_still_prints_generated_password(tmp_path, monkeypatch, capsys):
    """Regression: with ODYSSEUS_SKIP_ADMIN_PROMPT set in a real terminal, a
    random password was generated but never printed, locking the admin out."""
    import bcrypt

    setup_module = _load_setup_module()
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(auth_path))
    monkeypatch.delenv("ODYSSEUS_ADMIN_USER", raising=False)
    monkeypatch.delenv("ODYSSEUS_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("ODYSSEUS_SKIP_ADMIN_PROMPT", "1")
    monkeypatch.setattr(setup_module.sys, "stdin", _TtyStdin())

    assert setup_module.create_default_admin() == "created"

    password = _generated_password(capsys.readouterr().out)
    assert password, "generated password was not printed"
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert bcrypt.checkpw(password.encode(), data["users"]["admin"]["password_hash"].encode())


def test_prompt_eof_falls_back_to_generated_password(tmp_path, monkeypatch, capsys):
    """Regression: a TTY-looking but closed stdin raised EOFError from input()
    and aborted admin creation with a misleading permissions message."""
    import bcrypt

    setup_module = _load_setup_module()
    auth_path = tmp_path / "auth.json"
    monkeypatch.setattr(setup_module, "AUTH_FILE", str(auth_path))
    monkeypatch.delenv("ODYSSEUS_ADMIN_USER", raising=False)
    monkeypatch.delenv("ODYSSEUS_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ODYSSEUS_SKIP_ADMIN_PROMPT", raising=False)
    monkeypatch.setattr(setup_module.sys, "stdin", _TtyStdin())

    def _eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)

    assert setup_module.create_default_admin() == "created"

    password = _generated_password(capsys.readouterr().out)
    assert password, "generated password was not printed"
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    assert bcrypt.checkpw(password.encode(), data["users"]["admin"]["password_hash"].encode())
