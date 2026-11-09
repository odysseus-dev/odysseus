"""Email: the inbox lists a seeded message, opens it, and marks it read.

Email is the one area with no way to reach a real account deterministically,
and the repo already solved that: `routes/email_routes.py` carries a
fixture path gated on `ODYSSEUS_EMAIL_FIXTURE=1` plus a fixture file in
the data dir. This uses that mechanism rather than inventing a second
one - which means it also only covers what the fixture covers. Real IMAP
sync and SMTP send stay out, and say so in the table's gap list.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.constants import DATA_DIR

LIST_PATH = "/api/email/list"
READ_PATH = "/api/email/read"
MARK_READ_PATH = "/api/email/mark-read"
UNREAD_STATE_PATH = "/api/email/unread-state"

# The filename the fixture path reads. Same value as
# routes/email_routes.py's `_fixture_email_file`.
FIXTURE_FILENAME = "fixture_email_messages.json"

SUBJECT = "Odysseus smoke inbox message"
BODY = "Body of the smoke fixture message."
SENDER = "Smoke Sender <smoke@example.invalid>"


@pytest.fixture
def seeded_inbox(client, account):
    """Write the fixture inbox, and put back whatever was there before.

    The flag itself has to be in the app's environment, which is the
    launcher's job; if it is missing the fixture path stays off and the
    list route falls through to a real account that does not exist. That
    reads as a skip, not a failure.
    """
    path = Path(DATA_DIR) / FIXTURE_FILENAME
    previous = path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"messages": [{
        "owner": account["username"],
        "from": SENDER,
        "subject": SUBJECT,
        "date": "2026-09-29T12:00:00+00:00",
        "body": BODY,
    }]}, indent=2) + "\n", encoding="utf-8")
    try:
        yield path
    finally:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)


def _fixture_rows(client):
    response = client.get(LIST_PATH, params={"folder": "INBOX", "limit": 10})
    assert response.status_code == 200, response.text
    body = response.json()
    rows = [e for e in body.get("emails") or [] if e.get("subject") == SUBJECT]
    if not rows:
        pytest.skip(
            "the instance is not serving the email fixture, so there is no "
            "deterministic inbox to read. Boot it with ODYSSEUS_EMAIL_FIXTURE=1 "
            "(scripts/odysseus-smoke does)."
        )
    return rows


def test_the_inbox_lists_opens_and_marks_a_message(client, seeded_inbox):
    row = _fixture_rows(client)[0]
    uid = row["uid"]
    assert row.get("from_address") == "smoke@example.invalid", row
    assert row.get("is_read") is False, row

    read = client.get(f"{READ_PATH}/{uid}", params={"folder": "INBOX"})
    assert read.status_code == 200, read.text
    opened = read.json()
    assert opened.get("subject") == SUBJECT, opened
    assert BODY in str(opened.get("body") or ""), opened
    assert BODY in str(opened.get("body_html") or ""), opened

    before = client.get(UNREAD_STATE_PATH, params={"folder": "INBOX"})
    assert before.status_code == 200, before.text
    assert before.json().get("unread_count") == 1, before.text

    marked = client.post(f"{MARK_READ_PATH}/{uid}", params={"folder": "INBOX"})
    assert marked.status_code == 200, marked.text

    after = client.get(UNREAD_STATE_PATH, params={"folder": "INBOX"})
    assert after.json().get("unread_count") == 0, after.text
