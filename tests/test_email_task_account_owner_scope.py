"""Regression tests for explicit email-account ownership in scheduled tasks."""

from unittest import mock

import pytest


def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_account(
    Factory,
    account_id,
    owner,
    imap_user,
    *,
    enabled=True,
    is_default=False,
):
    from core.database import EmailAccount

    db = Factory()
    db.add(
        EmailAccount(
            id=account_id,
            owner=owner,
            name=account_id,
            enabled=enabled,
            is_default=is_default,
            imap_host="imap.example.com",
            imap_user=imap_user,
            smtp_host="smtp.example.com",
            smtp_user=imap_user,
            from_address=imap_user,
        )
    )
    db.commit()
    db.close()


def _legacy_config():
    return {
        "imap_host": "legacy-imap.example.com",
        "imap_user": "legacy@example.com",
        "imap_password": "legacy-secret",
        "smtp_host": "legacy-smtp.example.com",
        "smtp_user": "legacy@example.com",
        "smtp_password": "legacy-secret",
        "email_from": "legacy@example.com",
    }


@pytest.mark.asyncio
async def test_email_task_rejects_foreign_explicit_account(monkeypatch):
    from routes import email_pollers

    imap_calls = []

    monkeypatch.setattr(
        email_pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True},
    )
    monkeypatch.setattr(email_pollers, "_account_visible_to_task_owner", lambda *_args: False)

    def fake_imap_connect(account_id=None, owner=""):
        imap_calls.append((account_id, owner))
        raise AssertionError(
            "IMAP must not be opened for a foreign account"
        )

    monkeypatch.setattr(
        email_pollers,
        "_imap_connect",
        fake_imap_connect,
    )

    with pytest.raises(
        PermissionError,
        match="not available to this task owner",
    ):
        await email_pollers._auto_summarize_pass_single(
            account_id="bob-account",
            owner="alice",
        )

    assert imap_calls == []


@pytest.mark.asyncio
async def test_email_task_rejects_disabled_explicit_account(monkeypatch):
    """A disabled selection must not be replaced by another enabled account."""
    from routes import email_pollers

    Factory = _make_db()
    _make_account(
        Factory,
        "alice-disabled",
        "alice",
        "alice-disabled@example.com",
        enabled=False,
    )
    _make_account(
        Factory,
        "alice-default",
        "alice",
        "alice-default@example.com",
        is_default=True,
    )

    monkeypatch.setattr(
        email_pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True},
    )
    monkeypatch.setattr(
        email_pollers,
        "_imap_connect",
        lambda *_args, **_kwargs: pytest.fail(
            "a disabled explicit account must fail before opening IMAP"
        ),
    )

    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(
            PermissionError,
            match="not available to this task owner",
        ):
            await email_pollers._auto_summarize_pass_single(
                account_id="alice-disabled",
                owner="alice",
            )


@pytest.mark.asyncio
async def test_email_task_with_no_visible_accounts_does_not_enter_single_pass(monkeypatch):
    """An owner-scoped fan-out must not reinterpret zero rows as legacy mode."""
    from routes import email_pollers
    from routes.email_helpers import EmailNotConfiguredError

    Factory = _make_db()
    _make_account(Factory, "bob-account", "bob", "bob@example.com")
    monkeypatch.setattr(
        email_pollers,
        "_auto_summarize_pass_single",
        lambda **_kwargs: pytest.fail(
            "zero visible owner accounts must fail before the single-account path"
        ),
    )

    with mock.patch("core.database.SessionLocal", Factory):
        with pytest.raises(
            EmailNotConfiguredError,
            match="No email accounts are available to this task owner",
        ):
            await email_pollers._auto_summarize_pass(owner="alice")


@pytest.mark.asyncio
async def test_unscoped_zero_account_fanout_preserves_legacy_single_pass(monkeypatch):
    """Single-user callers without an owner keep the legacy fallback path."""
    from routes import email_pollers

    Factory = _make_db()
    calls = []

    async def fake_single(**kwargs):
        calls.append((kwargs["account_id"], kwargs["owner"]))
        return "legacy processed"

    monkeypatch.setattr(email_pollers, "_auto_summarize_pass_single", fake_single)

    with mock.patch("core.database.SessionLocal", Factory):
        result = await email_pollers._auto_summarize_pass()

    assert result == "legacy processed"
    assert calls == [(None, "")]


@pytest.mark.asyncio
async def test_email_task_fanout_propagates_account_discovery_failure(monkeypatch):
    """A scoped discovery error must not be converted into legacy selection."""
    from core import database
    from routes import email_pollers

    class FailingSession:
        def __init__(self):
            self.closed = False

        def query(self, _model):
            raise RuntimeError("database unavailable")

        def close(self):
            self.closed = True

    db = FailingSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        email_pollers,
        "_auto_summarize_pass_single",
        lambda **_kwargs: pytest.fail(
            "discovery failures must not enter the single-account path"
        ),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await email_pollers._auto_summarize_pass(owner="alice")

    assert db.closed is True


def test_scoped_email_config_does_not_use_deployment_legacy_credentials(monkeypatch):
    """A user with no visible account must not inherit shared legacy secrets."""
    from routes import email_helpers

    Factory = _make_db()
    monkeypatch.setattr(email_helpers, "_load_settings", _legacy_config)

    with mock.patch("core.database.SessionLocal", Factory):
        cfg = email_helpers._get_email_config(owner="alice")

    assert cfg.get("account_id") is None
    assert cfg.get("imap_host") == ""
    assert cfg.get("imap_password") == ""
    assert cfg.get("smtp_host") == ""
    assert cfg.get("smtp_password") == ""


def test_unscoped_email_config_preserves_single_user_legacy_fallback(monkeypatch):
    """The fail-closed guard must retain legacy config for single-user calls."""
    from routes import email_helpers

    Factory = _make_db()
    monkeypatch.setattr(email_helpers, "_load_settings", _legacy_config)

    with mock.patch("core.database.SessionLocal", Factory):
        cfg = email_helpers._get_email_config()

    assert cfg.get("account_id") is None
    assert cfg.get("imap_host") == "legacy-imap.example.com"
    assert cfg.get("imap_password") == "legacy-secret"
    assert cfg.get("smtp_host") == "legacy-smtp.example.com"
    assert cfg.get("smtp_password") == "legacy-secret"


def test_scoped_email_config_propagates_account_lookup_failure(monkeypatch):
    """A database failure must not switch an owner-scoped call to legacy secrets."""
    from core import database
    from routes import email_helpers

    class FailingSession:
        def __init__(self):
            self.closed = False

        def query(self, _model):
            raise RuntimeError("database unavailable")

        def close(self):
            self.closed = True

    db = FailingSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        email_helpers,
        "_load_settings",
        lambda: pytest.fail("scoped lookup failures must not read legacy settings"),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        email_helpers._get_email_config(owner="alice")

    assert db.closed is True


def test_explicit_disabled_config_does_not_fall_back_to_default(monkeypatch):
    """An explicit disabled ID must not silently resolve another account."""
    from routes import email_helpers

    Factory = _make_db()
    _make_account(
        Factory,
        "alice-disabled",
        "alice",
        "alice-disabled@example.com",
        enabled=False,
    )
    _make_account(
        Factory,
        "alice-default",
        "alice",
        "alice-default@example.com",
        is_default=True,
    )
    monkeypatch.setattr(email_helpers, "_load_settings", _legacy_config)

    with mock.patch("core.database.SessionLocal", Factory):
        cfg = email_helpers._get_email_config(
            account_id="alice-disabled",
            owner="alice",
        )

    assert cfg.get("account_id") is None
    assert cfg.get("imap_host") == ""
    assert cfg.get("smtp_host") == ""


@pytest.mark.asyncio
async def test_email_task_allows_matching_explicit_account(monkeypatch):
    from routes import email_pollers

    imap_calls = []

    class FakeImap:
        def select(self, *_args, **_kwargs):
            return "OK", []

        def uid(self, *_args, **_kwargs):
            return "OK", [b""]

        def logout(self):
            return None

    def fake_imap_connect(account_id=None, owner=""):
        imap_calls.append((account_id, owner))
        return FakeImap()

    monkeypatch.setattr(
        email_pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True},
    )
    monkeypatch.setattr(email_pollers, "_account_visible_to_task_owner", lambda *_args: True)
    monkeypatch.setattr(
        email_pollers,
        "_imap_connect",
        fake_imap_connect,
    )
    monkeypatch.setattr(
        email_pollers,
        "_latest_inbox_fallback_uids",
        lambda conn, _reconnect: ([], conn),
    )

    result = await email_pollers._auto_summarize_pass_single(
        account_id="alice-account",
        owner="alice",
    )

    assert result == "No recent emails"
    assert imap_calls == [("alice-account", "alice")]


@pytest.mark.asyncio
async def test_email_task_fanout_filters_accounts_by_owner(monkeypatch):
    from core import database
    from routes import email_pollers

    class FakeColumn:
        def __eq__(self, _other):
            return True

        def asc(self):
            return self

        def desc(self):
            return self

    class FakeEmailAccount:
        enabled = FakeColumn()
        is_default = FakeColumn()
        created_at = FakeColumn()

    class Row:
        def __init__(self, account_id, name, owner, imap_user):
            self.id = account_id
            self.name = name
            self.owner = owner
            self.imap_user = imap_user
            self.from_address = imap_user

    rows = [
        Row("alice-primary", "Alice primary", "alice", "alice"),
        Row("legacy-alice", "Legacy Alice", "", "alice"),
        Row("bob-primary", "Bob primary", "bob", "bob"),
        Row("legacy-other", "Legacy other", "", "other"),
        Row("alice-secondary", "Alice secondary", "alice", "alice"),
    ]

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(rows)

    class FakeDb:
        def __init__(self):
            self.closed = False

        def query(self, model):
            assert model is FakeEmailAccount
            return FakeQuery()

        def close(self):
            self.closed = True

    db = FakeDb()
    calls = []

    async def fake_single(**kwargs):
        calls.append((kwargs["account_id"], kwargs["owner"]))
        return "processed"

    monkeypatch.setattr(database, "EmailAccount", FakeEmailAccount)
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        email_pollers,
        "_auto_summarize_pass_single",
        fake_single,
    )

    result = await email_pollers._auto_summarize_pass(owner="alice")

    assert calls == [
        ("alice-primary", "alice"),
        ("legacy-alice", "alice"),
        ("alice-secondary", "alice"),
    ]
    assert result == (
        "[Alice primary] processed\n"
        "[Legacy Alice] processed\n"
        "[Alice secondary] processed"
    )
    assert db.closed is True


@pytest.mark.asyncio
async def test_email_task_explicit_accounts_use_canonical_legacy_visibility(monkeypatch):
    """Explicit IDs use the same mailbox-match policy as account fan-out."""
    from routes import email_pollers

    Factory = _make_db()
    _make_account(Factory, "alice-owned", "alice", "alice")
    _make_account(Factory, "legacy-alice", "", "alice")
    _make_account(Factory, "bob-owned", "bob", "bob")
    _make_account(Factory, "legacy-other", "", "other")

    class FakeImap:
        def select(self, *_args, **_kwargs):
            return "OK", []

        def uid(self, *_args, **_kwargs):
            return "OK", [b""]

        def logout(self):
            return None

    imap_calls = []

    def fake_imap_connect(account_id=None, owner=""):
        imap_calls.append((account_id, owner))
        return FakeImap()

    monkeypatch.setattr(
        email_pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True},
    )
    monkeypatch.setattr(email_pollers, "_imap_connect", fake_imap_connect)
    monkeypatch.setattr(
        email_pollers,
        "_latest_inbox_fallback_uids",
        lambda conn, _reconnect: ([], conn),
    )

    with mock.patch("core.database.SessionLocal", Factory):
        assert await email_pollers._auto_summarize_pass_single(
            account_id="alice-owned",
            owner="alice",
        ) == "No recent emails"
        assert await email_pollers._auto_summarize_pass_single(
            account_id="legacy-alice",
            owner="alice",
        ) == "No recent emails"
        for account_id in ("bob-owned", "legacy-other", "missing"):
            with pytest.raises(PermissionError, match="not available to this task owner"):
                await email_pollers._auto_summarize_pass_single(
                    account_id=account_id,
                    owner="alice",
                )

    assert imap_calls == [
        ("alice-owned", "alice"),
        ("legacy-alice", "alice"),
    ]


def test_email_task_account_authorization_propagates_database_errors(monkeypatch):
    """Database failures must not be reported as ordinary access denials."""
    from core import database
    from routes import email_pollers

    class FailingSession:
        def __init__(self):
            self.closed = False

        def query(self, _model):
            raise RuntimeError("database unavailable")

        def close(self):
            self.closed = True

    db = FailingSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    with pytest.raises(RuntimeError, match="database unavailable"):
        email_pollers._account_visible_to_task_owner("alice-account", "alice")

    assert db.closed is True
