"""Quoted display-name commas must never become SMTP envelope recipients."""

import email
import sqlite3

import pytest


@pytest.mark.parametrize("as_lists", [False, True])
def test_mcp_send_parses_to_cc_bcc_without_exposing_bcc(monkeypatch, as_lists):
    import mcp_servers.email_server as es

    captured = {}

    class Smtp:
        def send_message(self, message, from_addr, to_addrs):
            captured.update(message=message, sender=from_addr, recipients=to_addrs)

        def quit(self):
            captured["closed"] = True

    monkeypatch.setattr(es, "_read_agent_email_confirm_setting", lambda: False)
    monkeypatch.setattr(es, "_resolve_send_config", lambda account: (
        "acct-test", {"from_address": "sender@example.test", "account_id": "acct-test"},
    ))
    monkeypatch.setattr(es, "_smtp_connect", lambda *args, **kwargs: Smtp())
    monkeypatch.setattr(es, "_imap_connect", lambda *args: (_ for _ in ()).throw(OSError("no inbox")))
    to = ['"Doe, Jane" <jane@example.test>', 'bob@example.test']
    cc = ['"Smith, Sam" <sam@example.test>']
    bcc = ['"Private, Pat" <pat@example.test>']
    result = es._send_email(
        to if as_lists else ", ".join(to), "Test", "Body",
        cc=cc if as_lists else ", ".join(cc),
        bcc=bcc if as_lists else ", ".join(bcc),
    )

    expected = ["jane@example.test", "bob@example.test", "sam@example.test", "pat@example.test"]
    assert captured["recipients"] == expected
    assert result["sent"] is True
    assert result["to"] == expected
    assert captured["closed"] is True
    assert captured["message"]["Bcc"] is None
    assert email.utils.getaddresses([str(captured["message"]["To"])]) == [
        ("Doe, Jane", "jane@example.test"), ("", "bob@example.test"),
    ]


def test_scheduled_send_parses_display_names_from_stored_headers(tmp_path, monkeypatch):
    import routes.email_helpers as helpers
    import routes.email_pollers as pollers

    db_path = tmp_path / "scheduled.db"
    monkeypatch.setattr(helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(pollers, "SCHEDULED_DB", db_path)
    helpers._init_scheduled_db()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO scheduled_emails
            (id, to_addr, cc, bcc, subject, body, attachments, send_at, created_at,
             status, account_id, owner)
            VALUES ('quoted', ?, ?, ?, 'Test', 'Body', '[]', '2000-01-01',
                    '2000-01-01', 'pending', 'acct-test', 'test-owner')
        """, ('"Doe, Jane" <jane@example.test>, bob@example.test',
              '"Smith, Sam" <sam@example.test>', '"Private, Pat" <pat@example.test>'))
    captured = {}

    def config(account, owner=""):
        assert (account, owner) == ("acct-test", "test-owner")
        return {"from_address": "sender@example.test"}

    def send(cfg, sender, recipients, message):
        captured.update(recipients=recipients, message=email.message_from_string(message))

    monkeypatch.setattr(pollers, "_get_email_config", config)
    monkeypatch.setattr(pollers, "_send_smtp_message", send)
    monkeypatch.setattr(pollers, "_imap", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no inbox")))
    monkeypatch.setattr(pollers, "_cleanup_compose_uploads", lambda attachments: None)

    assert pollers._scheduled_poll_once() == {"sent": ["quoted"], "failed": []}
    assert captured["recipients"] == [
        "jane@example.test", "bob@example.test", "sam@example.test", "pat@example.test",
    ]
    assert captured["message"]["Bcc"] is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM scheduled_emails").fetchone() == ("sent",)
