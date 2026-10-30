"""The inbox page must be chosen by date, not by IMAP UID order.

The list path selects which messages to fetch by taking the highest UIDs, which
assumes a server hands out UIDs in date order. Proton Mail Bridge assigns them in
Proton message-id order instead, so recent mail can sit at low UIDs and never
reach the page at all — on a live 2,934-message INBOX, the newest-by-UID page of
50 kept only 2 of the truly newest 50. `_index_ordered_uid_window` defers the
window to the durable index when that index provably covers the folder, and
returns None otherwise so the caller keeps the old UID-ordered window.
"""

import sqlite3


DAY = 86400
BASE = 1_767_225_600  # 2026-01-01T00:00:00+00:00


def _prepare(tmp_path, monkeypatch):
    import routes.email_helpers as email_helpers
    import routes.email_routes as email_routes

    db_path = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", db_path)
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", db_path)
    email_helpers._init_scheduled_db()
    return email_routes, db_path


def _seed(email_routes, db_path, uid_epoch_rows):
    """Insert index rows; UID order and date order are set independently."""
    account_key = email_routes._account_cache_key("acct-a", "alice")
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            """
            INSERT INTO email_message_index
            (owner, account_key, folder, uid, message_id, subject, date_epoch, updated_at)
            VALUES ('alice', ?, 'INBOX', ?, ?, ?, ?, '2026-01-01')
            """,
            [
                (account_key, str(uid), f"<m{uid}@example.com>", f"subject {uid}", float(epoch))
                for uid, epoch in uid_epoch_rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


# UID 1 is the newest message and UID 5 the oldest: ascending UIDs, descending
# dates, which is the shape Proton Mail Bridge produces.
BRIDGE_MAILBOX = [(uid, BASE + (5 - uid) * DAY) for uid in (1, 2, 3, 4, 5)]


def test_bridge_shaped_mailbox_pages_by_date_not_uid(tmp_path, monkeypatch):
    email_routes, db_path = _prepare(tmp_path, monkeypatch)
    _seed(email_routes, db_path, BRIDGE_MAILBOX)

    window = email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 0, 3, 5)

    assert window == [b"1", b"2", b"3"]      # the three newest by date
    assert window != [b"5", b"4", b"3"]      # what newest-by-UID would have fetched


def test_second_page_continues_in_date_order(tmp_path, monkeypatch):
    email_routes, db_path = _prepare(tmp_path, monkeypatch)
    _seed(email_routes, db_path, BRIDGE_MAILBOX)

    assert email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 3, 3, 5) == [b"4", b"5"]


def test_partial_index_defers_to_uid_order(tmp_path, monkeypatch):
    """A cold index cannot vouch for messages it has never seen."""
    email_routes, db_path = _prepare(tmp_path, monkeypatch)
    _seed(email_routes, db_path, BRIDGE_MAILBOX[:2])

    assert email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 0, 3, 5) is None


def test_filters_outside_the_index_defer(tmp_path, monkeypatch):
    email_routes, db_path = _prepare(tmp_path, monkeypatch)
    _seed(email_routes, db_path, BRIDGE_MAILBOX)

    assert email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "unread", 0, 3, 5) is None
    assert email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 0, 3, 0) is None


def test_page_wider_than_the_index_read_cap_defers(tmp_path, monkeypatch):
    """`_email_index_list` reads at most 200 rows, so an oversized page falls back."""
    email_routes, db_path = _prepare(tmp_path, monkeypatch)
    _seed(email_routes, db_path, [(uid, BASE + (250 - uid) * DAY) for uid in range(1, 251)])

    assert email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 0, 250, 250) is None
    page = email_routes._index_ordered_uid_window("alice", "acct-a", "INBOX", "all", 0, 200, 250)
    assert page is not None and len(page) == 200
