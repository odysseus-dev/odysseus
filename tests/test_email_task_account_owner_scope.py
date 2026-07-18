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


def _make_account(Factory, account_id, owner, imap_user):
    from core.database import EmailAccount

    db = Factory()
    db.add(
        EmailAccount(
            id=account_id,
            owner=owner,
            name=account_id,
            enabled=True,
            imap_host="imap.example.com",
            imap_user=imap_user,
            smtp_host="smtp.example.com",
            smtp_user=imap_user,
            from_address=imap_user,
        )
    )
    db.commit()
    db.close()


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
