"""Outgoing mail must carry a Message-ID anchored to the From address domain.

``email.utils.make_msgid()`` without a ``domain`` argument falls back to
``socket.getfqdn()``. Inside a container that is a non-resolvable hostname
(a container id on the docker network, or ``odysseus.local``), and several
receiving providers accept the SMTP delivery and then silently discard the
message as spam: the send log looks successful, the Sent folder gets its
copy, and nothing arrives. A missing Message-ID header fares no better.

The interactive compose path already derives the domain from the From
address (``routes/email/email_routes.py`` ``/send``). These tests pin the
same behaviour for the agent MCP send path, the scheduled-email poller and
the away auto-reply, which previously used bare ``make_msgid()`` (or no
Message-ID at all).
"""
import sqlite3
from email.parser import Parser

import pytest

pytest.importorskip("mcp")


def _assert_from_domain(header_value, domain):
    assert header_value, "Message-ID header missing from outgoing message"
    assert header_value.strip().endswith(f"@{domain}>"), (
        f"Message-ID {header_value!r} is not anchored to the From domain {domain!r}"
    )


def test_mcp_send_email_message_id_uses_from_domain(monkeypatch, tmp_path):
    import mcp_servers.email_server as es

    monkeypatch.setenv("EMAIL_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: False)
    monkeypatch.setattr(
        es,
        "_resolve_send_config",
        lambda account=None: (
            "acct-1",
            {
                "from_address": "agent@gmail.com",
                "account_id": "acct-1",
                "account_name": "Test",
                "smtp_host": "smtp.example.com",
            },
        ),
    )

    sent = []

    class FakeSMTP:
        def send_message(self, msg, from_addr=None, to_addrs=None):
            sent.append(msg)

        def quit(self):
            pass

    def _no_imap(account):
        raise RuntimeError("no imap in test")

    monkeypatch.setattr(es, "_smtp_connect", lambda account, cfg=None: FakeSMTP())
    monkeypatch.setattr(es, "_imap_connect", _no_imap)

    result = es._send_email("dest@example.com", "Subject", "Body")

    assert result["sent"] is True
    assert len(sent) == 1
    _assert_from_domain(result["message_id"], "gmail.com")


def test_scheduled_send_message_id_uses_from_domain(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_pollers as email_pollers

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO scheduled_emails
        (id, to_addr, subject, body, attachments, send_at, created_at, status, account_id, owner)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            "sched-mid-1",
            "recipient@example.com",
            "Subject",
            "Body",
            "[]",
            "2000-01-01T00:00:00",
            "1999-12-31T00:00:00",
            "acct-alice",
            "alice",
        ),
    )
    conn.commit()
    conn.close()

    captured = []

    def fake_get_email_config(account_id=None, owner=""):
        return {
            "from_address": "alice@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_user": "alice@example.com",
            "smtp_password": "secret",
        }

    def fake_send_smtp_message(cfg, from_addr, recipients, message, timeout=30):
        captured.append(message)

    class FakeImap:
        def __init__(self, account_id=None, owner=""):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def append(self, folder, flags, date_time, message):
            pass

    monkeypatch.setattr(email_pollers, "_get_email_config", fake_get_email_config)
    monkeypatch.setattr(email_pollers, "_send_smtp_message", fake_send_smtp_message)
    monkeypatch.setattr(email_pollers, "_imap", FakeImap)
    monkeypatch.setattr(email_pollers, "_detect_sent_folder", lambda imap: "Sent")
    monkeypatch.setattr(email_pollers, "_cleanup_compose_uploads", lambda attachments: None)

    result = email_pollers._scheduled_poll_once()

    assert result["sent"] == ["sched-mid-1"], result
    assert len(captured) == 1
    outgoing = Parser().parsestr(captured[0])
    _assert_from_domain(outgoing["Message-ID"], "example.com")


def test_away_reply_message_id_uses_from_domain(monkeypatch):
    import routes.email_pollers as email_pollers

    incoming = Parser().parsestr(
        "From: Bob <bob@example.org>\r\n"
        "To: Alice <alice@example.com>\r\n"
        "Subject: Hi\r\n"
        "Message-ID: <orig@example.org>\r\n"
        "\r\n"
        "Hello\r\n"
    )

    captured = []

    def fake_get_email_config(account_id=None, owner=""):
        return {
            "from_address": "alice@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_user": "alice@example.com",
            "smtp_password": "secret",
        }

    def fake_send_smtp_message(cfg, from_addr, recipients, message, timeout=30):
        captured.append(message)

    monkeypatch.setattr(email_pollers, "_get_email_config", fake_get_email_config)
    monkeypatch.setattr(email_pollers, "_send_smtp_message", fake_send_smtp_message)
    monkeypatch.setattr(email_pollers, "_away_reply_already_sent", lambda *a, **k: False)
    monkeypatch.setattr(email_pollers, "_sender_is_automated", lambda msg, addr: False)
    monkeypatch.setattr(email_pollers, "_record_away_reply", lambda *a, **k: None)

    ok, detail = email_pollers._send_away_reply(
        {}, "alice", "acct-alice", incoming, "<orig@example.org>",
        "Bob <bob@example.org>", "Hi",
    )

    assert ok, detail
    assert len(captured) == 1
    outgoing = Parser().parsestr(captured[0])
    _assert_from_domain(outgoing["Message-ID"], "example.com")
