"""Regression: reserved sentinel usernames must not be registerable.

`core.middleware.require_admin` grants admin to any request whose
`current_user == "internal-tool"` (the in-process tool-loopback sentinel),
and the cookie auth path in app.py sets `current_user` to the raw username.
Before this fix nothing reserved that name, so a self-service signup (or an
admin typo) creating the account "internal-tool" was silently treated as an
admin by every `require_admin`-gated route — a privilege escalation. "api"
is reserved for the same reason (bearer-token owner attribution collision).

See the privilege-escalation finding from the 2026-06 code review.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.owner_identity import DEFAULT_LOCAL_OWNER
from tests.helpers.import_state import clear_module

_RESERVED_NAMES = ["internal-tool", "api", "demo", "system", DEFAULT_LOCAL_OWNER]


def _fresh_auth_manager(tmp_path):
    # Same import dance as test_security_regressions: drop any cached stub so
    # we exercise the real module from disk rather than a conftest mock.
    clear_module("core.auth")
    from core.auth import AuthManager

    return AuthManager(str(tmp_path / "auth.json"))


@pytest.mark.parametrize(
    "name",
    _RESERVED_NAMES + ["INTERNAL-TOOL", " Internal-Tool ", "Api", "SYSTEM"],
)
def test_create_user_rejects_reserved_usernames(tmp_path, name):
    mgr = _fresh_auth_manager(tmp_path)
    assert mgr.create_user(name, "pw-123456") is False
    # The normalized name must not have been written to the user table.
    assert name.strip().lower() not in mgr.users


def test_create_user_rejects_empty_username(tmp_path):
    mgr = _fresh_auth_manager(tmp_path)
    assert mgr.create_user("   ", "pw-123456") is False
    assert "" not in mgr.users


@pytest.mark.parametrize("name", _RESERVED_NAMES)
def test_setup_rejects_reserved_admin_username(tmp_path, name):
    mgr = _fresh_auth_manager(tmp_path)
    # First-run admin setup funnels through create_user, so it's covered too.
    assert mgr.setup(name, "pw-123456") is False
    assert mgr.is_configured is False


@pytest.mark.parametrize("name", _RESERVED_NAMES)
def test_rename_into_reserved_username_is_blocked(tmp_path, name):
    mgr = _fresh_auth_manager(tmp_path)
    assert mgr.create_user("admin", "pw-123456", is_admin=True) is True
    assert mgr.create_user("bob", "pw-123456") is True
    assert mgr.rename_user("bob", name, "admin") is False
    assert name not in mgr.users
    assert "bob" in mgr.users


@pytest.mark.parametrize("name", _RESERVED_NAMES)
def test_legacy_reserved_username_is_removed_on_load(tmp_path, name):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"users": {"%s": {"password_hash": "unused", "is_admin": false}, '
        '"admin": {"password_hash": "unused", "is_admin": true}}}' % name,
        encoding="utf-8",
    )
    mgr = _fresh_auth_manager(tmp_path)

    assert name not in mgr.users
    assert "admin" in mgr.users
    assert name not in auth_path.read_text(encoding="utf-8")


def test_legacy_reserved_username_session_cannot_authenticate(tmp_path):
    auth_path = tmp_path / "auth.json"
    sessions_path = tmp_path / "sessions.json"
    auth_path.write_text(
        '{"users": {"internal-tool": {"password_hash": "unused", "is_admin": false}}}',
        encoding="utf-8",
    )
    sessions_path.write_text(
        '{"tok": {"username": "internal-tool", "expiry": 9999999999}}',
        encoding="utf-8",
    )
    mgr = _fresh_auth_manager(tmp_path)

    assert mgr.validate_token("tok") is False
    assert mgr.get_username_for_token("tok") is None


def test_legacy_reserved_username_session_cannot_pass_admin_gate(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    sessions_path = tmp_path / "sessions.json"
    auth_path.write_text(
        '{"users": {"internal-tool": {"password_hash": "unused", "is_admin": false}, '
        '"admin": {"password_hash": "unused", "is_admin": true}}}',
        encoding="utf-8",
    )
    sessions_path.write_text(
        '{"tok": {"username": "internal-tool", "expiry": 9999999999}}',
        encoding="utf-8",
    )
    mgr = _fresh_auth_manager(tmp_path)
    clear_module("core.middleware")
    from core.middleware import require_admin

    monkeypatch.setenv("AUTH_ENABLED", "true")
    request = SimpleNamespace(
        state=SimpleNamespace(current_user=mgr.get_username_for_token("tok")),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=mgr)),
    )

    assert request.state.current_user is None
    with pytest.raises(HTTPException) as exc:
        require_admin(request)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("name", _RESERVED_NAMES)
def test_legacy_reserved_single_user_migrates_to_admin(tmp_path, name):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"username": "%s", "password_hash": "unused"}' % name,
        encoding="utf-8",
    )
    mgr = _fresh_auth_manager(tmp_path)

    assert name not in mgr.users
    assert "admin" in mgr.users
    assert mgr.is_admin("admin") is True


def test_token_cache_owner_normalization_requires_current_user():
    clear_module("core.auth")
    from core.auth import normalize_known_username

    users = {"alice": {}, "admin": {}}

    assert normalize_known_username(users, " Alice ") == "alice"
    for name in _RESERVED_NAMES:
        assert normalize_known_username(users, name) is None
    assert normalize_known_username(users, "") is None


def test_normal_usernames_still_allowed(tmp_path):
    mgr = _fresh_auth_manager(tmp_path)
    assert mgr.create_user("alice", "pw-123456") is True
    assert "alice" in mgr.users
