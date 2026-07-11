"""Regression: two concurrent callers of `_scheduled_poll_once` (the
in-process 30s poller and the `odysseus-mail poll-scheduled` CLI, which the
project's own docstrings warn can race on the same SQLite when
ODYSSEUS_INPROCESS_POLLERS is left enabled alongside an external cron/systemd
driver) must not both send the same scheduled email.

The old code selected pending rows, then only updated their status to 'sent'
*after* the SMTP send completed - two overlapping calls can both SELECT the
same 'pending' row before either UPDATEs it, so both send it. The fix adds
an atomic claim step (`UPDATE ... SET status='sending' WHERE status='pending'`)
before any work happens; only the caller whose UPDATE actually changes a row
proceeds, the other sees rowcount == 0 and skips it.

This test drives two real threads through the real `_scheduled_poll_once`
against a shared SQLite file, synchronized with a barrier so both reach the
SELECT at (as close to) the same moment as possible, and asserts the send
callback fired exactly once.
"""
import sqlite3
import threading
import time


def test_concurrent_pollers_do_not_double_send(tmp_path, monkeypatch):
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
            "sched-race-1",
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

    send_calls = []
    send_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def fake_get_email_config(account_id=None, owner=""):
        return {
            "from_address": "alice@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_user": "alice@example.com",
            "smtp_password": "secret",
        }

    def fake_send_smtp_message(*args, **kwargs):
        # Widen the window between the claim and the actual send so a
        # buggy (unclaimed) second poller has every opportunity to also
        # get past its SELECT and attempt to send.
        time.sleep(0.05)
        with send_lock:
            send_calls.append(threading.get_ident())

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

    results = []

    def _run():
        barrier.wait()
        results.append(email_pollers._scheduled_poll_once())

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(send_calls) == 1, (
        f"expected exactly one send for the racing pollers, got {len(send_calls)}: "
        "the second poller must lose the atomic claim and skip the row"
    )

    conn = sqlite3.connect(db_path)
    status = conn.execute(
        "SELECT status FROM scheduled_emails WHERE id=?", ("sched-race-1",)
    ).fetchone()[0]
    conn.close()
    assert status == "sent"
