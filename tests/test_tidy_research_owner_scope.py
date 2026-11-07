"""Owner-scope tests for action_tidy_research.

Broken research files (empty or unparseable JSON) have no readable owner
stamp. The HTTP path and _find_owned_research_path treat parse failure as
not-owned, so a regular user's tidy task must not unlink them. Clearing a
corrupt record is a privileged act instead: admins, and the operator in
single-user mode (AUTH_ENABLED=false), keep the janitor.

Every case installs a fake AuthManager rather than relying on ambient state.
Setting AUTH_ENABLED=true alone leaves is_configured False, so the admin check
these tests exist to pin would never be reached.
"""

import json

import pytest

from src.builtin_actions import TaskNoop, action_tidy_research


@pytest.fixture
def research_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr("src.builtin_actions.DEEP_RESEARCH_DIR", str(data_dir))
    return data_dir


def _install_auth(monkeypatch, *, is_configured: bool):
    """Auth enabled, with `root` as the only admin. `is_configured` False models
    the pre-setup window, where no admin exists yet."""

    import core.auth as core_auth

    configured = is_configured

    class FakeAuthManager:
        is_configured = configured

        def is_admin(self, user):
            return user == "root"

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(core_auth, "AuthManager", FakeAuthManager)


@pytest.fixture
def configured_auth(monkeypatch):
    _install_auth(monkeypatch, is_configured=True)


def _write(data_dir, name: str, text: str):
    path = data_dir / f"{name}.json"
    path.write_text(text, encoding="utf-8")
    return path


def _write_owned(data_dir, name: str, owner: str):
    return _write(data_dir, name, json.dumps({"owner": owner, "query": name}))


@pytest.mark.asyncio
async def test_configured_non_admin_skips_unparseable_and_empty_files(
    research_dir, configured_auth
):
    alice = _write_owned(research_dir, "alice-ok", "alice")
    bob = _write_owned(research_dir, "bob-ok", "bob")
    empty = _write(research_dir, "broken-empty", "")
    garbage = _write(research_dir, "broken-json", "{not json")
    alice_txt = alice.read_text(encoding="utf-8")
    bob_txt = bob.read_text(encoding="utf-8")

    with pytest.raises(TaskNoop, match="not permitted"):
        await action_tidy_research("alice")

    assert alice.exists()
    assert alice.read_text(encoding="utf-8") == alice_txt
    assert bob.exists()
    assert bob.read_text(encoding="utf-8") == bob_txt
    assert empty.exists(), "empty file has no owner stamp; non-admin tidy must not unlink it"
    assert garbage.exists(), "unparseable file has no owner stamp; non-admin tidy must not unlink it"


@pytest.mark.asyncio
async def test_configured_non_admin_skip_reason_does_not_claim_none_broken(
    research_dir, configured_auth
):
    """The skip reason reaches Activity as `skipped -- <reason>`, so it must not
    report a clean scan over files it declined to look at."""
    _write(research_dir, "broken-empty", "")

    with pytest.raises(TaskNoop) as excinfo:
        await action_tidy_research("alice")

    assert "none broken" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_configured_admin_still_removes_broken_files(research_dir, configured_auth):
    valid = _write_owned(research_dir, "ok", "alice")
    empty = _write(research_dir, "broken-empty", "   ")
    garbage = _write(research_dir, "broken-json", "not-json")

    message, ok = await action_tidy_research("root")

    assert ok is True
    assert "Removed 2" in message
    assert valid.exists(), "admin sweep clears corrupt records, not valid ones"
    assert not empty.exists()
    assert not garbage.exists()


@pytest.mark.asyncio
async def test_pre_setup_denies_owner_who_would_be_admin_once_configured(
    research_dir, monkeypatch
):
    """Auth on, no admin created yet: fail closed even for `root`.

    The owner here is non-empty and would satisfy is_admin, so this fails if the
    is_configured guard is removed. An empty owner would not discriminate -- it
    is denied by the `owner and ...` short-circuit either way.
    """
    _install_auth(monkeypatch, is_configured=False)
    empty = _write(research_dir, "broken-empty", "")
    garbage = _write(research_dir, "broken-json", "{")

    with pytest.raises(TaskNoop, match="not permitted"):
        await action_tidy_research("root")

    assert empty.exists()
    assert garbage.exists()


@pytest.mark.asyncio
async def test_empty_owner_does_not_delete_broken_files(research_dir, configured_auth):
    empty = _write(research_dir, "broken-empty", "")
    garbage = _write(research_dir, "broken-json", "{")

    with pytest.raises(TaskNoop, match="not permitted"):
        await action_tidy_research("")

    assert empty.exists()
    assert garbage.exists()


@pytest.mark.asyncio
async def test_auth_disabled_still_removes_broken_files(research_dir, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    valid = _write_owned(research_dir, "ok", "alice")
    empty = _write(research_dir, "broken-empty", "   ")
    garbage = _write(research_dir, "broken-json", "not-json")

    message, ok = await action_tidy_research("alice")

    assert ok is True
    assert "Removed 2" in message
    assert valid.exists()
    assert not empty.exists()
    assert not garbage.exists()
