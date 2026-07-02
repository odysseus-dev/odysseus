"""Regression tests for read_file / write_file path confinement.

Covers:
  - /etc/shadow, /etc/passwd, /var/log — blocked (outside roots)
  - ~/.ssh/authorized_keys — blocked (sensitive subpath deny list)
  - Symlink that resolves into .ssh — blocked
  - Relative traversal (~/../../etc/passwd) — blocked
  - Shell rc files (.bashrc, .zshrc, .profile) — blocked
  - SSH key filenames (id_rsa, id_ed25519) — blocked regardless of dir
  - Legitimate paths under project data/ and /tmp — allowed
  - Extra roots via tool_path_extra_roots setting — opt-in
  - Even with $HOME as extra root, sensitive subpaths stay blocked
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _make_block(tool_type, content):
    return SimpleNamespace(tool_type=tool_type, content=content)


# ── Unit tests on _is_sensitive_path ──────────────────────────────────

def test_sensitive_ssh_dir():
    from src.tool_execution import _is_sensitive_path
    assert _is_sensitive_path("/home/user/.ssh/authorized_keys")
    assert _is_sensitive_path(os.path.expanduser("~") + "/.ssh/config")


def test_sensitive_gnupg_dir():
    from src.tool_execution import _is_sensitive_path
    assert _is_sensitive_path("/home/user/.gnupg/pubring.kbx")


def test_sensitive_shell_rc():
    from src.tool_execution import _is_sensitive_path
    assert _is_sensitive_path("/home/user/.bashrc")
    assert _is_sensitive_path("/home/user/.zshrc")
    assert _is_sensitive_path("/home/user/.profile")


def test_sensitive_key_filenames():
    from src.tool_execution import _is_sensitive_path
    assert _is_sensitive_path("/tmp/id_rsa")
    assert _is_sensitive_path("/tmp/id_ed25519")
    assert _is_sensitive_path("/tmp/authorized_keys")


def test_non_sensitive_path():
    from src.tool_execution import _is_sensitive_path
    assert not _is_sensitive_path("/tmp/notes.txt")
    assert not _is_sensitive_path("/home/user/projects/file.py")


def test_sensitive_case_insensitive():
    """On case-insensitive filesystems (Windows, default macOS) a case-variant
    name resolves to the same protected file, so the deny-list must match
    regardless of case. Built with os.path.join so the separator is right on
    both POSIX and Windows.
    """
    from src.tool_execution import _is_sensitive_path
    # sensitive directory, varied case
    assert _is_sensitive_path(os.path.join("home", "u", ".SSH", "authorized_keys"))
    assert _is_sensitive_path(os.path.join("home", "u", ".Gnupg", "pubring.kbx"))
    # sensitive filename, varied case
    assert _is_sensitive_path(os.path.join("ws", "AUTHORIZED_KEYS"))
    assert _is_sensitive_path(os.path.join("ws", "Id_Rsa"))
    assert _is_sensitive_path(os.path.join("ws", ".ENV"))
    assert _is_sensitive_path(os.path.join("ws", ".Env"))
    # both dir and file varied
    assert _is_sensitive_path(os.path.join("home", "u", ".SSH", "AUTHORIZED_KEYS"))
    # an ordinary file with none of the sensitive names is still allowed
    assert not _is_sensitive_path(os.path.join("ws", "Readme.md"))


# ── Unit tests on _resolve_tool_path ─────────────────────────────────

def test_blocks_etc_shadow():
    """The motivating example: /etc/shadow must be rejected."""
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="outside the allowed roots"):
        _resolve_tool_path("/etc/shadow")


def test_blocks_etc_passwd():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="outside the allowed roots"):
        _resolve_tool_path("/etc/passwd")


def test_blocks_var_log():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="outside the allowed roots"):
        _resolve_tool_path("/var/log/system.log")


def test_blocks_ssh_authorized_keys():
    """~/.ssh/authorized_keys — blocked by sensitive-subpath deny even
    though $HOME is NOT a default root (the deny list fires first)."""
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path("~/.ssh/authorized_keys")


def test_blocks_ssh_dir_absolute():
    from src.tool_execution import _resolve_tool_path
    home = os.path.expanduser("~")
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path(os.path.join(home, ".ssh", "config"))


def test_blocks_symlink_into_ssh(tmp_path):
    """A symlink under /tmp that points into ~/.ssh must be caught
    because realpath resolves the link before the deny-list check."""
    from src.tool_execution import _resolve_tool_path
    ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    link = tmp_path / "ssh_link"
    try:
        link.symlink_to(ssh_dir)
    except OSError:
        pytest.skip("cannot create symlink")
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path(str(link))


def test_blocks_traversal_outside_roots():
    """~/../../etc/passwd — after tilde expansion and .. resolution the
    path lands outside every allowed root."""
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError):
        _resolve_tool_path("~/../../etc/passwd")


def test_blocks_bashrc():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path("~/.bashrc")


def test_blocks_zshrc():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path("~/.zshrc")


def test_blocks_env_file():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path("~/.env")


def test_blocks_netrc():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path("~/.netrc")


def test_allows_project_data(tmp_path):
    """Paths under project data/ must resolve cleanly."""
    from src.tool_execution import _resolve_tool_path
    from src.constants import DATA_DIR
    target = os.path.join(DATA_DIR, "test-confinement-ok.txt")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(target, "w") as f:
        f.write("ok")
    try:
        resolved = _resolve_tool_path(target)
        assert resolved == os.path.realpath(target)
    finally:
        os.unlink(target)


def test_allows_tmp(tmp_path):
    """Paths under /tmp (or its realpath) must resolve cleanly."""
    from src.tool_execution import _resolve_tool_path
    f = tmp_path / "confinement-test.txt"
    f.write_text("ok")
    resolved = _resolve_tool_path(str(f))
    assert resolved == os.path.realpath(str(f))


def test_rejects_empty_path():
    from src.tool_execution import _resolve_tool_path
    with pytest.raises(ValueError, match="path is required"):
        _resolve_tool_path("")
    with pytest.raises(ValueError, match="path is required"):
        _resolve_tool_path("   ")


def test_extra_roots_opt_in(tmp_path):
    """When tool_path_extra_roots includes a directory, paths under it
    are allowed (but sensitive subpaths are still blocked)."""
    from src.tool_execution import _resolve_tool_path
    extra_dir = tmp_path / "extra_root"
    extra_dir.mkdir()
    target = extra_dir / "file.txt"
    target.write_text("ok")

    with patch("src.settings.get_setting", return_value=[str(extra_dir)]):
        resolved = _resolve_tool_path(str(target))
        assert resolved == os.path.realpath(str(target))


def test_extra_root_still_blocks_sensitive(tmp_path):
    """Even when $HOME is in tool_path_extra_roots, ~/.ssh/authorized_keys
    must still be rejected by the sensitive-subpath deny list."""
    from src.tool_execution import _resolve_tool_path
    home = os.path.expanduser("~")
    with patch("src.settings.get_setting", return_value=[home]):
        with pytest.raises(ValueError, match="sensitive directory"):
            _resolve_tool_path("~/.ssh/authorized_keys")


# ── Integration: dispatch-level tests ────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_dispatch_blocks_etc_shadow(monkeypatch):
    """End-to-end: read_file dispatch must reject /etc/shadow."""
    auth_mod = sys.modules.get("core.auth")
    if auth_mod is None:
        import core.auth as _real_auth
        auth_mod = _real_auth

    class _AdminAuth:
        is_configured = True
        def is_admin(self, username):
            return True

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: _AdminAuth())
    monkeypatch.setattr(
        "src.tool_execution.owner_is_admin_or_single_user",
        lambda owner: True,
    )

    from src.tool_execution import execute_tool_block
    desc, result = await execute_tool_block(
        _make_block("read_file", "/etc/shadow"),
        owner="admin-user",
    )
    assert "outside the allowed roots" in (result.get("error") or "")
    assert result.get("exit_code") == 1


@pytest.mark.asyncio
async def test_write_file_dispatch_blocks_authorized_keys(monkeypatch):
    """End-to-end: write_file dispatch must reject ~/.ssh/authorized_keys."""
    auth_mod = sys.modules.get("core.auth")
    if auth_mod is None:
        import core.auth as _real_auth
        auth_mod = _real_auth

    class _AdminAuth:
        is_configured = True
        def is_admin(self, username):
            return True

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: _AdminAuth())
    monkeypatch.setattr(
        "src.tool_execution.owner_is_admin_or_single_user",
        lambda owner: True,
    )

    from src.tool_execution import execute_tool_block
    desc, result = await execute_tool_block(
        _make_block("write_file", "~/.ssh/authorized_keys\nssh-rsa AAAAB3..."),
        owner="admin-user",
    )
    assert "sensitive directory" in (result.get("error") or "")
    assert result.get("exit_code") == 1


@pytest.mark.asyncio
async def test_write_file_dispatch_blocks_cron(monkeypatch):
    """End-to-end: write_file to /etc/cron.d must be rejected."""
    auth_mod = sys.modules.get("core.auth")
    if auth_mod is None:
        import core.auth as _real_auth
        auth_mod = _real_auth

    class _AdminAuth:
        is_configured = True
        def is_admin(self, username):
            return True

    monkeypatch.setattr(auth_mod, "AuthManager", lambda: _AdminAuth())
    monkeypatch.setattr(
        "src.tool_execution.owner_is_admin_or_single_user",
        lambda owner: True,
    )

    from src.tool_execution import execute_tool_block
    desc, result = await execute_tool_block(
        _make_block("write_file", "/etc/cron.d/agent-payload\n* * * * * root /tmp/p\n"),
        owner="admin-user",
    )
    assert "outside the allowed roots" in (result.get("error") or "")
    assert result.get("exit_code") == 1
