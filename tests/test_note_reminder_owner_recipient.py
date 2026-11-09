"""Reminder emails for non-admins never use the install-wide recipient.

``reminder_email_to`` is the admin's own address (hidden from non-admin
settings reads). A non-admin's due note must go to the owner's own mail
account, or be skipped, through both the fire-reminder route and the
background note scanner, and no address may reach a response or a log line.

The ntfy topic and webhook integration are install-wide too (there is no
per-user one), so a non-admin's ntfy/webhook reminder is skipped, and no
webhook/ntfy result or log line quotes the integration id, URL or topic.
"""

import asyncio
import json
import logging
import smtplib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

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


# `_get_email_config` copies an already-resolved account id into its
# install-wide settings.json/env fallback when the row read fails part-way,
# and stamps that dict with the caller as `owner`. Ownership must come from
# the account row itself, never from that dict.
def _fallback_cfg_with_account(leaked_id):
    def fake_get_email_config(account_id=None, owner=None):
        return {
            "account_id": leaked_id,
            "account_name": "legacy",
            "owner": owner or "",
            "smtp_host": "smtp.install.test",
            "smtp_port": 465,
            "smtp_user": "install-user",
            "smtp_password": "install-password",
            "from_address": INSTALL_FROM,
        }
    return fake_get_email_config


@pytest.mark.parametrize("path", ["route", "scanner"])
@pytest.mark.parametrize("owner", ["bob", "carol"])
def test_fallback_cfg_with_unowned_account_id_skips_email(reminders, monkeypatch, owner, path):
    import routes.email_routes as email_routes
    from src.builtin_actions import TaskNoop

    monkeypatch.setattr(email_routes, "_get_email_config", _fallback_cfg_with_account("acct-admin"))

    if path == "route":
        result = reminders.fire(owner)
        assert result["email_sent"] is False
        assert result["email_error"] == "No email account of your own is set up for reminder emails"
        _assert_absent(json.dumps(result))
    else:
        with pytest.raises(TaskNoop) as exc:
            reminders.scan(owner)
        _assert_absent(str(exc.value))

    assert reminders.sent == []
    _assert_absent(_logs(reminders.caplog))


def test_fallback_cfg_with_own_account_id_sends_from_the_row_address(reminders, monkeypatch):
    import routes.email_routes as email_routes

    monkeypatch.setattr(email_routes, "_get_email_config", _fallback_cfg_with_account("acct-bob"))

    result = reminders.fire("bob")

    assert result["email_sent"] is True
    assert reminders.sent == [(BOB_ADDR, [BOB_ADDR])]
    _assert_absent(json.dumps(result))
    _assert_absent(_logs(reminders.caplog), (ADMIN_INBOX, INSTALL_FROM))


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


# --- ntfy / webhook: install-wide channels -------------------------------

NTFY_BASE = "http://93.184.216.34"  # public literal: no DNS lookup
NTFY_TOPIC = "admin-topic-9c1e"
NTFY_ID = "intg-ntfy-41"
WEBHOOK_URL = "http://93.184.216.35/hooks/admin-hook-path"
WEBHOOK_ID = "intg-hook-7f3a"
CHANNEL_TARGETS = (
    "93.184.216.34", NTFY_TOPIC, NTFY_ID,
    "93.184.216.35", "admin-hook-path", WEBHOOK_ID,
)
SKIP_MESSAGE = {
    "ntfy": "Phone reminders are configured by the admin",
    "webhook": "Webhook reminders are configured by the admin",
}
FAILURE_MESSAGE = {
    "ntfy": "Reminder ntfy push could not be sent; see the server log",
    "webhook": "Reminder webhook could not be sent; see the server log",
}
_CHANNEL_SETTINGS = {
    "ntfy": {"reminder_channel": "ntfy", "reminder_ntfy_topic": NTFY_TOPIC},
    "webhook": {
        "reminder_channel": "webhook",
        "reminder_webhook_integration_id": WEBHOOK_ID,
        "reminder_webhook_payload_template": '{"content": "{{message}}"}',
    },
}


@pytest.fixture
def push(reminders, monkeypatch):
    """`reminders` with the channel switched to ntfy/webhook, the install-wide
    integrations patched in and an httpx spy (as in test_reminder_ntfy_ssrf)."""
    import httpx
    from src import settings

    state = SimpleNamespace(calls=[], fail=False, ntfy_base=NTFY_BASE, webhook_url=WEBHOOK_URL)

    class _SpyAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kw):
            state.calls.append(url)
            if state.fail:
                raise httpx.ConnectError(f"All connection attempts failed: {url}")
            resp = MagicMock()
            resp.is_success = True
            resp.status_code = 200
            return resp

    monkeypatch.setattr("src.integrations.load_integrations", lambda: [
        {"id": NTFY_ID, "preset": "ntfy", "enabled": True, "name": "ntfy",
         "base_url": state.ntfy_base, "api_key": "ntfy-secret"},
        {"id": WEBHOOK_ID, "preset": "generic", "enabled": True, "name": "admin hook",
         "base_url": state.webhook_url},
    ])
    monkeypatch.setattr(httpx, "AsyncClient", _SpyAsyncClient)

    def use(channel, **extra):
        monkeypatch.setattr(settings, "load_settings", lambda: {
            "reminder_llm_synthesis": False, **_CHANNEL_SETTINGS[channel], **extra,
        })

    return SimpleNamespace(
        use=use, state=state, fire=reminders.fire, scan=reminders.scan,
        caplog=reminders.caplog,
    )


@pytest.mark.parametrize("channel", ["ntfy", "webhook"])
def test_route_non_admin_push_channel_is_skipped(push, channel):
    push.use(channel)

    result = push.fire("bob")

    assert push.state.calls == []
    assert result[f"{channel}_sent"] is False
    assert result[f"{channel}_error"] == SKIP_MESSAGE[channel]
    _assert_absent(json.dumps(result), CHANNEL_TARGETS)
    _assert_absent(_logs(push.caplog), CHANNEL_TARGETS)


@pytest.mark.parametrize("channel", ["ntfy", "webhook"])
def test_scanner_non_admin_push_channel_sends_nothing(push, channel):
    from src.builtin_actions import TaskNoop

    push.use(channel)

    with pytest.raises(TaskNoop) as exc:
        push.scan("bob")

    assert push.state.calls == []
    _assert_absent(str(exc.value), CHANNEL_TARGETS)
    _assert_absent(_logs(push.caplog), CHANNEL_TARGETS)


@pytest.mark.parametrize("path", ["route", "scanner"])
@pytest.mark.parametrize("channel", ["ntfy", "webhook"])
def test_admin_push_channel_still_sends(push, channel, path):
    push.use(channel)

    if path == "route":
        result = push.fire("admin")
        assert result[f"{channel}_sent"] is True
        assert result[f"{channel}_error"] == ""
    else:
        _message, ok = push.scan("admin")
        assert ok is True

    expected = f"{NTFY_BASE}/{NTFY_TOPIC}" if channel == "ntfy" else WEBHOOK_URL
    assert push.state.calls == [expected]


@pytest.mark.parametrize("channel", ["ntfy", "webhook"])
def test_push_send_failure_response_and_log_carry_no_target(push, channel):
    push.use(channel)
    push.state.fail = True

    result = push.fire("admin")

    assert len(push.state.calls) == 1, "the send must have been attempted"
    assert result[f"{channel}_sent"] is False
    assert result[f"{channel}_error"] == FAILURE_MESSAGE[channel]
    _assert_absent(json.dumps(result), CHANNEL_TARGETS)
    logs = _logs(push.caplog)
    assert "ConnectError" in logs
    _assert_absent(logs, CHANNEL_TARGETS)


@pytest.mark.parametrize("channel", ["ntfy", "webhook"])
def test_rejected_push_url_is_not_quoted(push, channel):
    metadata = "169.254.169.254"
    push.use(channel)
    push.state.ntfy_base = f"http://{metadata}"
    push.state.webhook_url = f"http://{metadata}/hooks/admin-hook-path"

    result = push.fire("admin")

    assert push.state.calls == []
    assert result[f"{channel}_sent"] is False
    assert "rejected" in result[f"{channel}_error"].lower()
    _assert_absent(json.dumps(result), CHANNEL_TARGETS + (metadata,))
    _assert_absent(_logs(push.caplog), CHANNEL_TARGETS + (metadata,))


def test_missing_webhook_integration_is_not_quoted(push):
    gone = "intg-hook-gone-55"
    push.use("webhook", reminder_webhook_integration_id=gone)

    result = push.fire("admin")

    assert push.state.calls == []
    assert result["webhook_sent"] is False
    assert result["webhook_error"]
    _assert_absent(json.dumps(result), CHANNEL_TARGETS + (gone,))


# --- the email-urgency scanner's reminder (builtin_actions) ---------------

@pytest.fixture
def urgency(reminders, monkeypatch, tmp_path):
    """`reminders` plus the email-urgency scan wired to one fake urgent unread
    message per account (the IMAP double from test_email_urgency_checkpoint)."""
    import routes.email_helpers as email_helpers
    from src import builtin_actions, llm_core, task_endpoint
    from tests.test_email_urgency_checkpoint import _FakeImap

    monkeypatch.setattr(builtin_actions, "EMAIL_URGENCY_CACHE_DIR", str(tmp_path / "urgency-cache"))
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", tmp_path / "scheduled-emails.db")
    monkeypatch.setattr(
        email_helpers, "_imap_connect",
        lambda account_id=None, **_kwargs: _FakeImap(str(account_id), set(), set(), {}),
    )
    monkeypatch.setattr(
        task_endpoint, "resolve_task_candidates",
        lambda *args, **kwargs: [("http://llm", "model", {})],
    )

    async def urgent(*_args, **_kwargs):
        return '{"score": 3, "reason": "urgent"}'

    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", urgent)

    def scan(owner):
        try:
            return asyncio.run(builtin_actions.action_check_email_urgency(owner))
        except builtin_actions.TaskNoop as exc:
            return str(exc), False

    return SimpleNamespace(scan=scan, sent=reminders.sent, caplog=reminders.caplog)


def test_urgency_reminder_for_non_admin_goes_to_own_account(urgency):
    message, _ok = urgency.scan("bob")

    assert urgency.sent == [(BOB_ADDR, [BOB_ADDR])]
    _assert_absent(message, (ADMIN_INBOX, INSTALL_FROM, ADMIN_FROM))
    _assert_absent(_logs(urgency.caplog), (ADMIN_INBOX, INSTALL_FROM, ADMIN_FROM))


def test_urgency_reminder_for_non_admin_never_falls_back_to_install_address(urgency, monkeypatch):
    # The owner's account resolves to the install-wide settings.json SMTP
    # config (ready to send, with `email_from` and `reminder_email_to` set):
    # the urgency reminder is skipped rather than sent there.
    import routes.email_routes as email_routes

    monkeypatch.setattr(email_routes, "_get_email_config", _fallback_cfg_with_account(None))

    message, _ok = urgency.scan("bob")

    logs = _logs(urgency.caplog)
    assert "non-admin owner has no email account of their own" in logs, "the reminder was never dispatched"
    assert urgency.sent == []
    _assert_absent(message)
    _assert_absent(logs)
