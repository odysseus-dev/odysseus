"""An auth store written by this process counts as established, like one it loaded.

Before, only `_load` set `_store_established`, so after first-run setup in
a fresh process a deleted auth.json was taken for a first run until the
next restart, and the account lifecycle check did not fail closed.
"""
import importlib

import pytest

import core.auth as ca


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    # The live module, not the one imported at collection: an earlier test
    # may have re-imported core.auth, and the code under test resolves it
    # at call time.
    ca = importlib.import_module("core.auth")
    monkeypatch.setattr(ca, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    return ca.AuthManager(str(tmp_path / "auth.json"))


def test_first_run_setup_establishes_the_store(mgr):
    assert not mgr._store_established
    assert mgr.setup("admin", "admin-pass-123")
    assert mgr._store_established


def test_a_vanished_store_after_setup_fails_closed(mgr):
    assert mgr.setup("admin", "admin-pass-123")
    assert mgr.create_user("alice", "alice-pass-123")
    import os
    os.remove(mgr.auth_path)
    revoked = importlib.import_module("core.note_writes").NotePermissionRevoked
    with pytest.raises(revoked) as exc:
        with mgr.account_mutation_guard(actor="alice", owner="alice"):
            pass
    assert exc.value.status_code == 503


def test_an_empty_store_is_still_a_first_run(mgr):
    mgr.signup_enabled = True  # saves a config with no accounts
    assert not mgr._store_established
    assert not mgr.is_configured
