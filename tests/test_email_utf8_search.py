"""Non-ASCII (e.g. Cyrillic) email search reaches IMAP as CHARSET UTF-8 literals.

Before the fix, `TEXT "рэйки"` raised UnicodeEncodeError inside imaplib (commands
are ASCII-encoded); the per-folder try/except swallowed it and search silently
returned nothing. Gmail also rejects raw UTF-8 inside quoted strings.
"""
import pytest

email_server = pytest.importorskip("mcp_servers.email_server")
email_routes = pytest.importorskip("routes.email_routes")


class FakeConn:
    """Minimal imaplib stand-in: records UID SEARCH calls and the pending literal."""

    def __init__(self, index):
        self.index = index  # term -> set of uids (bytes)
        self.literal = None
        self.calls = []

    def uid(self, cmd, *args):
        assert cmd == "SEARCH"
        lit, self.literal = self.literal, None
        self.calls.append((args, lit))
        if args[:3] == ("CHARSET", "UTF-8", "TEXT"):
            assert lit is not None, "non-ASCII term must be sent as a literal"
            term = lit.decode("utf-8")
            return "OK", [b" ".join(sorted(self.index.get(term, set()), key=int))]
        # ASCII path: criteria string must be pure ASCII (what imaplib can encode)
        crit = args[1]
        crit.encode("ascii")
        return "OK", [b"7 8"]


@pytest.mark.parametrize("mod", [email_server, email_routes])
def test_single_cyrillic_term_uses_utf8_literal(mod):
    conn = FakeConn({"рэйки": {b"3", b"10", b"2"}})
    status, data = mod._uid_search_text_terms(conn, ["рэйки"])
    assert status == "OK"
    assert data == [b"2 3 10"]  # numeric order, not lexical
    args, lit = conn.calls[0]
    assert args == ("CHARSET", "UTF-8", "TEXT")
    assert lit == "рэйки".encode("utf-8")


@pytest.mark.parametrize("mod", [email_server, email_routes])
def test_multiple_terms_are_intersected(mod):
    conn = FakeConn({"рэйки": {b"1", b"2", b"3"}, "курс": {b"2", b"3", b"9"}})
    status, data = mod._uid_search_text_terms(conn, ["рэйки", "курс"])
    assert status == "OK" and data == [b"2 3"]
    assert len(conn.calls) == 2


@pytest.mark.parametrize("mod", [email_server, email_routes])
def test_no_match_returns_empty_ok(mod):
    conn = FakeConn({})
    assert mod._uid_search_text_terms(conn, ["отчёт"]) == ("OK", [b""])


def test_server_error_is_propagated():
    class Bad(FakeConn):
        def uid(self, cmd, *args):
            self.literal = None
            return "NO", [b"charset not supported"]

    assert email_server._uid_search_text_terms(Bad({}), ["рэйки"])[0] == "NO"


def _run_agent_search(monkeypatch, query, index):
    conn = FakeConn(index)
    conn.select = lambda *a, **k: ("OK", [b"1"])
    conn.logout = lambda: None
    conn.close = lambda: None
    conn.state = "SELECTED"
    monkeypatch.setattr(email_server, "_fixture_search_emails", lambda *a, **k: None)
    monkeypatch.setattr(email_server, "_get_cached_summaries", lambda: {})
    monkeypatch.setattr(email_server, "_imap_connect", lambda account=None: conn)
    monkeypatch.setattr(email_server, "_resolve_folder", lambda c, f, role=None: f)
    fetched = []

    def fake_uid(cmd, *args):
        if cmd == "FETCH":
            fetched.append(args[0])
            return "NO", [None]  # skip header parsing; we only assert which UIDs were fetched
        return FakeConn.uid(conn, cmd, *args)

    conn.uid = fake_uid
    email_server._search_emails(query, folders=["INBOX"], max_results=5)
    return conn, fetched


def test_agent_search_cyrillic_goes_through_literal(monkeypatch):
    conn, fetched = _run_agent_search(monkeypatch, "рэйки", {"рэйки": {b"4", b"5"}})
    assert conn.calls[0][0] == ("CHARSET", "UTF-8", "TEXT")
    assert conn.calls[0][1] == "рэйки".encode("utf-8")
    assert fetched == [b"5", b"4"]  # newest first, as before


def test_agent_search_ascii_path_unchanged(monkeypatch):
    conn, fetched = _run_agent_search(monkeypatch, "Deep Research", {})
    args, lit = conn.calls[0]
    assert lit is None and args[0] is None
    assert args[1] == '(OR OR FROM "Deep Research" SUBJECT "Deep Research" TEXT "Deep Research")'
    assert fetched == [b"8", b"7"]


def test_ui_terms_split_for_cyrillic_query():
    # The UI route passes _email_search_terms(q) to the helper: phrases stay whole.
    assert email_routes._email_search_terms('"исходный отчёт" рэйки') == ["исходный отчёт", "рэйки"]
