"""Regression tests for explicit email-account ownership in scheduled tasks."""

import pytest


@pytest.mark.asyncio
async def test_email_task_rejects_foreign_explicit_account(monkeypatch):
    from routes import email_pollers

    imap_calls = []

    monkeypatch.setattr(
        email_pollers,
        "_load_settings",
        lambda: {"email_auto_summarize": True},
    )
    monkeypatch.setattr(
        email_pollers,
        "_owner_for_email_account",
        lambda account_id: "bob",
    )

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
    monkeypatch.setattr(
        email_pollers,
        "_owner_for_email_account",
        lambda account_id: "alice",
    )
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
        def __init__(self, account_id, name, owner):
            self.id = account_id
            self.name = name
            self.owner = owner

    rows = [
        Row("alice-primary", "Alice primary", "alice"),
        Row("bob-primary", "Bob primary", "bob"),
        Row("alice-secondary", "Alice secondary", "alice"),
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
        ("alice-secondary", "alice"),
    ]
    assert result == (
        "[Alice primary] processed\n"
        "[Alice secondary] processed"
    )
    assert db.closed is True
