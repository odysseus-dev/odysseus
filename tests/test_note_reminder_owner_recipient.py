"""Reminder emails for non-admins never use the install-wide recipient.

``reminder_email_to`` is the admin's own address (hidden from non-admin
settings reads). A non-admin's due note must go to the owner's own mail
account, or be skipped, through both the fire-reminder route and the
background note scanner, and no address may reach a response or a log line.
"""

import asyncio
import json
import logging
import smtplib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.helpers.database import disposable_database


ADMIN_INBOX = "admin-inbox@example.test"
INSTALL_FROM = "install-from@example.test"
ADMIN_FROM = "admin-mail@example.test"
BOB_ADDR = "bob-mail@example.test"
ALL_ADDRESSES = (ADMIN_INBOX, INSTALL_FROM, ADMIN_FROM, BOB_ADDR)


class _AuthManager:
    is_configured = True
    admins = {"admin"}

    def __init__(self, *args, **kwargs):
        pass

    def is_admin(self, user):
        return user in self.admins


class _Request:
    def __init__(self, body, *, user):
        self._body = body
        self.state = SimpleNamespace(current_user=user)
        self.client = SimpleNamespace(host="127.0.0.1")
        self.headers = {}
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=_AuthManager()))

    async def json(self):
        return self._body


@pytest.fixture
def reminders(tmp_path, monkeypatch, caplog):
    from core import database
    from routes import note_routes
    import routes.email_helpers as email_helpers
    from src import builtin_actions, settings

    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr("core.auth.AuthManager", _AuthManager)
    monkeypatch.setattr(settings, "load_settings", lambda: {
        "reminder_channel": "email",
        "reminder_llm_synthesis": False,
        "reminder_email_to": ADMIN_INBOX,
    })
    # The install-wide legacy SMTP config every owner without an account
    # row falls back to.
    monkeypatch.setattr(email_helpers, "_load_settings", lambda: {
        "smtp_host": "smtp.install.test",
        "smtp_user": "install-user",
        "smtp_password": "install-password",
        "email_from": INSTALL_FROM,
    })
    monkeypatch.setattr(note_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(note_routes, "_scheduler_ref", None)

    sent = []
    failure = {}

    def fake_send(cfg, sender, recipients, message):
        if failure.get("refuse"):
            raise smtplib.SMTPRecipientsRefused(
                {r: (550, f"5.1.1 <{r}> unknown, sender <{sender}>".encode()) for r in recipients}
            )
        sent.append((sender, list(recipients)))

    monkeypatch.setattr(email_helpers, "_send_smtp_message", fake_send)

    due = datetime.now(timezone.utc).isoformat()
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        monkeypatch.setattr(note_routes, "SessionLocal", factory)
        with factory() as db:
            for owner, address in (("bob", BOB_ADDR), ("admin", ADMIN_FROM)):
                db.add(database.EmailAccount(
                    id=f"acct-{owner}", owner=owner, name=f"{owner} mail",
                    is_default=True, enabled=True,
                    smtp_host="smtp.example.test", smtp_user=address,
                    from_address=address, oauth_provider="google",
                ))
            for owner in ("bob", "carol", "admin"):
                db.add(database.Note(
                    id=f"note-{owner}", owner=owner,
                    title=f"{owner} private note", content="secret body",
                    due_date=due,
                ))
            db.commit()

        router = note_routes.setup_note_routes()
        endpoint = next(
            route.endpoint for route in router.routes
            if route.path == "/api/notes/fire-reminder" and "POST" in route.methods
        )

        def fire(owner):
            return asyncio.run(endpoint(_Request({"note_id": f"note-{owner}"}, user=owner)))

        def scan(owner):
            return asyncio.run(builtin_actions.action_ping_notes(owner))

        yield SimpleNamespace(fire=fire, scan=scan, sent=sent, failure=failure, caplog=caplog)


def _assert_absent(text, addresses=ALL_ADDRESSES):
    for address in addresses:
        assert address not in text


def _logs(caplog):
    return "\n".join(record.getMessage() for record in caplog.records)


def test_route_non_admin_email_goes_to_own_account(reminders):
    result = reminders.fire("bob")

    assert reminders.sent == [(BOB_ADDR, [BOB_ADDR])]
    assert result["email_sent"] is True
    assert result["email_error"] == ""
    _assert_absent(json.dumps(result))
    _assert_absent(_logs(reminders.caplog), (ADMIN_INBOX,))


def test_route_non_admin_without_own_account_skips_email(reminders):
    result = reminders.fire("carol")

    assert reminders.sent == []
    assert result["email_sent"] is False
    assert result["email_error"] == "No email account of your own is set up for reminder emails"
    _assert_absent(json.dumps(result))
    _assert_absent(_logs(reminders.caplog))


def test_scanner_non_admin_owner_email_goes_to_own_account(reminders):
    message, ok = reminders.scan("bob")

    assert ok is True and "Pinged 1" in message
    assert reminders.sent == [(BOB_ADDR, [BOB_ADDR])]
    _assert_absent(message)
    _assert_absent(_logs(reminders.caplog), (ADMIN_INBOX,))


def test_scanner_non_admin_without_own_account_sends_nothing(reminders):
    from src.builtin_actions import TaskNoop

    with pytest.raises(TaskNoop) as exc:
        reminders.scan("carol")

    assert reminders.sent == []
    _assert_absent(str(exc.value))
    _assert_absent(_logs(reminders.caplog))


@pytest.mark.parametrize("path", ["route", "scanner"])
def test_admin_note_still_goes_to_install_recipient(reminders, path):
    if path == "route":
        result = reminders.fire("admin")
        assert result["email_sent"] is True
    else:
        _message, ok = reminders.scan("admin")
        assert ok is True

    assert reminders.sent == [(ADMIN_FROM, [ADMIN_INBOX])]


@pytest.mark.parametrize("owner", ["bob", "admin"])
def test_smtp_failure_response_and_log_carry_no_address(reminders, owner):
    reminders.failure["refuse"] = True

    result = reminders.fire(owner)

    assert result["email_sent"] is False
    assert result["email_error"] == "Reminder email could not be sent; see the server log"
    _assert_absent(json.dumps(result))
    logs = _logs(reminders.caplog)
    assert "SMTPRecipientsRefused" in logs
    _assert_absent(logs)
