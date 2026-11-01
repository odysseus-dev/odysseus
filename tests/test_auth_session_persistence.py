"""A valid login session cookie must survive a deploy restart of the server.

AuthManager keeps tokens in memory but persists them to sessions.json on create
and reloads them at startup, so a restart must NOT log the user out (the 302 the
bench saw was the restart gap / a client-side artifact, not session loss).
"""
from core import auth


def test_session_token_survives_a_restart(tmp_path):
    auth_path = str(tmp_path / "auth.json")
    first = auth.AuthManager(auth_path)
    assert first.create_user("pewds", "hunter2", is_admin=True)
    token = first.create_session_trusted("pewds")
    assert token and first.validate_token(token)

    # Simulate a process restart: a brand-new AuthManager on the same data dir.
    second = auth.AuthManager(auth_path)
    assert second.validate_token(token) is True
    assert second.get_username_for_token(token) == "pewds"


def test_revoked_token_does_not_survive_restart(tmp_path):
    auth_path = str(tmp_path / "auth.json")
    first = auth.AuthManager(auth_path)
    first.create_user("pewds", "hunter2", is_admin=True)
    token = first.create_session_trusted("pewds")
    first.revoke_token(token)

    second = auth.AuthManager(auth_path)
    assert second.validate_token(token) is False
