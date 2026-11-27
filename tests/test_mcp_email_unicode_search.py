"""Live MCP search must transmit Unicode, not report encoding errors as no mail."""
import asyncio
import imaplib

import pytest

from mcp_servers import email_server as es


class Mailbox:
    """An ASCII command channel with a separately framed UTF-8 literal port."""

    def __init__(self, query, matches=None, failure=None):
        self.query = query
        self.matches = {"TEXT": [2]} if matches is None else matches
        self.failure = failure
        self.literal = None
        self.searches = []
        self.fetches = []
        self.selected = []
        self.logged_out = False

    def select(self, folder, readonly=False):
        self.selected.append((folder, readonly))
        return "OK", [b"3"]

    def uid(self, operation, *args):
        if operation == "SEARCH":
            # Real imaplib's command encoder rejects raw non-ASCII strings.
            for arg in args:
                if isinstance(arg, str):
                    arg.encode("ascii")
            literal, self.literal = self.literal, None
            self.searches.append((args, literal))
            if literal is None:
                return "OK", [b"2"]
            assert literal == self.query.encode("utf-8")
            if getattr(self, "utf8_enabled", False):
                assert args[0] is None
            else:
                assert args[:2] == ("CHARSET", "UTF-8")
            key = next(key for key in self.matches if args[-1].endswith(key)) if any(
                args[-1].endswith(key) for key in self.matches) else ""
            if self.failure is not None and args[-1].endswith("SUBJECT"):
                if isinstance(self.failure, Exception):
                    raise self.failure
                return self.failure, [b"[BADCHARSET (US-ASCII)] unsupported"]
            return "OK", [b" ".join(str(n).encode() for n in self.matches.get(key, []))]
        assert operation == "FETCH"
        uid = int(args[0])
        self.fetches.append(uid)
        raw = (f"Subject: Owned message {uid}\r\nFrom: fixture@example.invalid\r\n"
               f"Date: Thu, 12 Nov 2026 08:{uid % 60:02d}:00 +0000\r\n"
               f"Message-ID: <owned-{uid}@example.invalid>\r\n\r\n").encode()
        return "OK", [(b"1 (RFC822.HEADER)", raw)]

    def logout(self):
        self.logged_out = True


@pytest.fixture
def mailbox(monkeypatch):
    monkeypatch.setattr(es, "_fixture_search_emails", lambda *a, **k: None)
    monkeypatch.setattr(es, "_indexed_search_emails", lambda *a, **k: None)
    monkeypatch.setattr(es, "_get_cached_summaries", lambda: {})
    def install(query, matches=None, failure=None):
        conn = Mailbox(query, matches, failure)
        monkeypatch.setattr(es, "_imap_connect", lambda account=None: conn)
        return conn
    return install


@pytest.mark.parametrize("query", ["счёт", "café", "東京", 'café "quote" \\ folder'])
def test_live_unicode_query_reaches_imap_and_keeps_folder(mailbox, query):
    conn = mailbox(query)
    rows = es._search_emails(query, folders=["Finance/2026"], account="owned")
    assert [row["uid"] for row in rows] == ["2"]
    assert rows[0]["_folder"] == "Finance/2026"
    assert conn.selected == [('"Finance/2026"', True)]
    assert conn.logged_out


def test_unicode_search_unions_all_existing_fields_once_in_uid_order(mailbox):
    conn = mailbox("café", {"FROM": [1], "SUBJECT": [2], "TEXT": [1, 2],
        "HEADER Content-Disposition": [10], "HEADER Content-Type": [10]})
    rows = es._search_emails("café", folders=["INBOX"], max_results=2)
    assert [row["uid"] for row in rows] == ["10", "2"]
    assert conn.fetches == [10, 2]
    assert len(conn.searches) == 5


@pytest.mark.parametrize("key", ["FROM", "SUBJECT", "TEXT", "HEADER Content-Disposition", "HEADER Content-Type"])
def test_unicode_match_is_found_in_each_existing_search_field(mailbox, key):
    mailbox("café", {key: [2]})
    assert [row["uid"] for row in es._search_emails("café", folders=["INBOX"])] == ["2"]


def test_unicode_search_keeps_conservative_and_exact_date_bounds(mailbox):
    conn = mailbox("café", {"TEXT": [1, 2, 3]})
    rows = es._search_emails("café", folders=["INBOX"],
        date_from="2026-11-12T08:02:00Z", date_to="2026-11-12T08:03:00Z")
    assert [row["uid"] for row in rows] == ["2"]
    assert all("SENTSINCE 11-Nov-2026" in args[-1] and
               "SENTBEFORE 14-Nov-2026" in args[-1] for args, _ in conn.searches)


def test_unicode_search_with_negotiated_utf8_accept_omits_charset(mailbox):
    conn = mailbox("café")
    conn.utf8_enabled = True
    assert [row["uid"] for row in es._search_emails("café", folders=["INBOX"])] == ["2"]
    assert all(args[0] is None for args, _ in conn.searches)


def test_unicode_successful_empty_search_is_a_real_no_match(mailbox):
    conn = mailbox("café", {})
    assert es._search_emails("café", folders=["INBOX"]) == []
    assert conn.fetches == [] and conn.logged_out


@pytest.mark.parametrize("failure", ["NO", "BAD", imaplib.IMAP4.error("unsupported charset"), OSError("offline")])
def test_unicode_search_failure_is_not_successful_no_match(mailbox, monkeypatch, failure):
    conn = mailbox("café", {"FROM": [1]}, failure=failure)
    monkeypatch.setattr(es, "_read_accounts_from_db", lambda: [])
    result = asyncio.run(es.call_tool("search_emails", {"query": "café", "folders": ["INBOX"]}))
    assert result[0].text.startswith("Error: Search failed:"), result[0].text
    assert conn.fetches == []  # Do not publish partial coverage from FROM.
    assert conn.logged_out


@pytest.mark.parametrize("query", ["receipt", 'quote "value"', "path\\file"])
def test_ascii_live_search_retains_one_command_and_all_fields(mailbox, query):
    conn = mailbox(query)
    assert [row["uid"] for row in es._search_emails(query, folders=["INBOX"])] == ["2"]
    assert len(conn.searches) == 1
    args, literal = conn.searches[0]
    assert args[0] is None and literal is None
    assert all(key in args[1] for key in ("FROM", "SUBJECT", "TEXT", "HEADER Content-Disposition", "HEADER Content-Type"))
    assert conn.logged_out
