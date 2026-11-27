import pathlib
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.email.email_helpers as email_helpers
import routes.email.email_routes as email_routes
from routes.email.email_routes import require_owner, setup_email_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_owner] = lambda: "owner@example.com"
    router = setup_email_routes()
    app.include_router(router)
    return TestClient(app)


def _raw_header_for(uid: str, subject: str) -> bytes:
    return (
        f"Message-ID: <msg-{uid}@example.com>\r\n"
        f"Subject: {subject}\r\n"
        f"From: Sender <sender@example.com>\r\n"
        f"To: owner@example.com\r\n"
        f"Date: Mon, 03 Aug 2026 10:0{uid}:00 +0000\r\n\r\n"
    ).encode("utf-8")


def test_full_imap_sync_reconciles_stale_rows_in_email_message_index(tmp_path, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_FIXTURE_EMAIL_FILE", raising=False)
    test_db = tmp_path / "scheduled_emails.db"
    monkeypatch.setattr(email_routes, "SCHEDULED_DB", test_db)
    monkeypatch.setattr(email_helpers, "SCHEDULED_DB", test_db)
    email_helpers._init_scheduled_db()

    server_messages = {
        "1": _raw_header_for("1", "TestMail"),
        "5": _raw_header_for("5", "Re: Testnachricht"),
        "9": _raw_header_for("9", "Active Message"),
    }
    search_status = ["OK"]

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        conn = MagicMock()

        def fake_uid(cmd, *args):
            cmd_up = cmd.upper()
            if cmd_up == "SEARCH":
                if search_status[0] != "OK":
                    return (search_status[0], [b""])
                criteria = args[-1] if args else "ALL"
                if "UNSEEN" in criteria:
                    uids = [u for u in server_messages if u == "9"]
                else:
                    uids = list(server_messages.keys())
                return ("OK", [" ".join(uids).encode("ascii")])
            if cmd_up == "FETCH":
                fetch_set = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
                requested = [u.strip() for u in fetch_set.split(",") if u.strip()]
                payload = []
                for idx, uid in enumerate(requested, start=1):
                    hdr = server_messages.get(uid)
                    if hdr is None:
                        continue
                    meta = f"{idx} (UID {uid} FLAGS () RFC822.SIZE {len(hdr)} RFC822.HEADER {{{len(hdr)}}}".encode("ascii")
                    payload.append((meta, hdr))
                    payload.append(b")")
                return ("OK", payload)
            return ("OK", [])

        conn.uid.side_effect = fake_uid
        yield conn

    def fake_imap_connect(account_id=None, owner=""):
        with fake_imap(account_id=account_id, owner=owner) as conn:
            conn.noop.return_value = ("OK", [])
            conn.select.return_value = ("OK", [b"3"])
            return conn

    with patch("routes.email.email_routes._imap_connect", side_effect=fake_imap_connect):
        client = _build_client()
        # 1. Initial live list populates SQLite email_message_index with UIDs 1, 5, 9
        r1 = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602"})
        assert r1.status_code == 200
        assert {e["uid"] for e in r1.json()["emails"]} == {"1", "5", "9"}

        # 2. Simulate deleting/moving UIDs 1 and 5 externally on the IMAP server
        del server_messages["1"]
        del server_messages["5"]

        # 3. Failed IMAP sync must NOT prune the local index
        search_status[0] = "NO"
        r_fail = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "refresh": 1})
        assert r_fail.status_code == 200
        r_cached_after_fail = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "cached_only": 1})
        assert {e["uid"] for e in r_cached_after_fail.json()["emails"]} == {"1", "5", "9"}

        # 4. Partial filter sync (unread) must NOT prune unmatched cached UIDs
        search_status[0] = "OK"
        r_unread = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "filter": "unread", "refresh": 1})
        assert r_unread.status_code == 200
        r_cached_after_partial = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "cached_only": 1})
        assert {e["uid"] for e in r_cached_after_partial.json()["emails"]} == {"1", "5", "9"}

        # 5. Successful full-folder IMAP refresh reconciles SQLite email_message_index
        r_refresh = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "refresh": 1})
        assert r_refresh.status_code == 200
        assert [e["uid"] for e in r_refresh.json()["emails"]] == ["9"]

        # 6. Reopening Email view (cached_only=1) no longer returns stale UIDs 1 and 5
        r_reopen = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "cached_only": 1})
        assert r_reopen.status_code == 200
        assert [e["uid"] for e in r_reopen.json()["emails"]] == ["9"]

        # 7. Emptying the IMAP folder completely clears the remaining cached row
        server_messages.clear()
        r_empty_refresh = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "refresh": 1})
        assert r_empty_refresh.status_code == 200
        assert r_empty_refresh.json()["emails"] == []

        r_reopen_empty = client.get("/api/email/list", params={"folder": "INBOX", "account_id": "acct-6602", "cached_only": 1})
        assert r_reopen_empty.status_code == 200
        assert r_reopen_empty.json()["emails"] == []
