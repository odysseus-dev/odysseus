"""Localized IMAP special-use folders stay opaque and role-driven."""

import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as email_mcp
from routes import email_routes


SENT_MUTF7 = "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"
ALL_MUTF7 = "&BBIEMAQ2BD0EPgQ9BD0ESwQ1-"
LIST_LINES = [
    br'(\HasNoChildren) "/" "INBOX"',
    f'(\\HasNoChildren \\sEnT) "/" "{SENT_MUTF7}"'.encode(),
    br'(\aRcHiVe) "/" "Archiv"',
    f'(\\ALL) "/" "{ALL_MUTF7}"'.encode(),
    br'(\tRaSh) NIL "Papelera"',
    '(\\JuNk) "/" "Courrier indésirable"'.encode(),
    br'(\DRAFTS) "/" "Brouillons"',
    br'(\fLaGgEd) "/" "Favoris"',
    br'(\HasNoChildren) "/" "Sentimental"',
    br'(\HasNoChildren) "/" "Archives 2024"',
]


class FakeConn:
    def __init__(self, lines=LIST_LINES):
        self.lines = lines
        self.list_calls = 0
        self.selects = []
        self.logged_out = False

    def list(self):
        self.list_calls += 1
        return "OK", self.lines

    def select(self, folder, readonly=False):
        self.selects.append((folder, readonly))
        return "OK", []

    def uid(self, command, *_args):
        if command.upper() == "SEARCH":
            return "OK", [b""]
        return "OK", []

    def noop(self):
        return "OK", []

    def logout(self):
        self.logged_out = True


@pytest.mark.parametrize("module", [email_mcp, email_routes])
def test_list_parser_preserves_modified_utf7_and_matches_flags_case_insensitively(module):
    name, attrs = module._parse_list_line(LIST_LINES[1])
    assert name == SENT_MUTF7
    assert attrs == frozenset({"\\hasnochildren", "\\sent"})
    assert module._folder_role_from_flags(LIST_LINES[1]) == "sent"


@pytest.mark.parametrize("module", [email_mcp, email_routes])
def test_all_special_use_flags_have_distinct_roles(module):
    expected = {
        "\\sent": "sent",
        "\\trash": "trash",
        "\\junk": "junk",
        "\\archive": "archive",
        "\\all": "all",
        "\\drafts": "drafts",
        "\\flagged": "flagged",
    }
    for flag, role in expected.items():
        assert module._folder_role_from_flags(f'({flag}) "/" "opaque-{role}"') == role
    assert module._folder_role_from_flags(r'(\HasNoChildren) "/" "Sentimental"') == ""
    assert module._folder_role_from_name("Sentimental") == ""
    assert module._folder_role_from_name("Archives 2024") == ""


@pytest.mark.parametrize(
    ("resolver", "module"),
    [
        (email_mcp._resolve_folder, email_mcp),
        (email_routes._resolve_mail_folder, email_routes),
    ],
)
def test_resolution_prefers_actual_name_then_flag_then_exact_legacy_candidate(resolver, module):
    conn = FakeConn()
    assert resolver(conn, SENT_MUTF7, "trash") == SENT_MUTF7
    assert resolver(conn, "Sent", "sent") == SENT_MUTF7
    assert resolver(conn, "All Mail", "all") == ALL_MUTF7
    assert resolver(conn, "Archive", "archive") == "Archiv"
    assert resolver(conn, "Archives 2024", module._folder_role_from_name("Archives 2024")) == "Archives 2024"
    assert resolver(conn, "Missing Label", "") == "Missing Label"


@pytest.mark.parametrize(
    "resolver",
    [email_mcp._resolve_folder, email_routes._resolve_mail_folder],
)
def test_archive_resolution_prefers_archive_but_falls_back_to_all(resolver):
    archive_and_all = FakeConn([
        br'(\All) "/" "Todo"',
        br'(\Archive) "/" "Archiv"',
    ])
    assert resolver(archive_and_all, "Archive", "archive") == "Archiv"

    all_only = FakeConn([br'(\All) "/" "Todo"'])
    assert resolver(all_only, "Archive", "archive") == "Todo"


def test_mcp_list_and_search_select_localized_special_use_mailboxes(monkeypatch):
    monkeypatch.setattr(email_mcp, "_fixture_email_enabled", lambda: False)
    monkeypatch.setattr(email_mcp, "_get_cached_summaries", lambda: {})
    list_conn = FakeConn()
    monkeypatch.setattr(email_mcp, "_imap_connect", lambda _account=None: list_conn)
    assert email_mcp._list_emails(folder="Sent") == []
    assert list_conn.selects == [(f'"{SENT_MUTF7}"', True)]
    assert list_conn.list_calls == 1

    search_conn = FakeConn()
    monkeypatch.setattr(email_mcp, "_imap_connect", lambda _account=None: search_conn)
    assert email_mcp._search_emails("needle") == []
    assert search_conn.selects == [
        ('"INBOX"', True),
        (f'"{SENT_MUTF7}"', True),
        (f'"{ALL_MUTF7}"', True),
        ('"Archiv"', True),
    ]
    assert search_conn.list_calls == 1


def _route_endpoint(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_rest_folder_api_returns_opaque_names_with_one_to_one_roles(monkeypatch, tmp_path):
    conn = FakeConn()

    @contextmanager
    def fake_imap(_account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_start_poller", lambda: None)
    monkeypatch.setattr(email_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    endpoint = _route_endpoint(email_routes.setup_email_routes(), "/api/email/folders")

    result = asyncio.run(endpoint(account_id="localized-folders", cached_only=0, owner="owner"))

    assert result["folders"] == [
        "INBOX",
        SENT_MUTF7,
        "Archiv",
        ALL_MUTF7,
        "Papelera",
        "Courrier indésirable",
        "Brouillons",
        "Favoris",
        "Sentimental",
        "Archives 2024",
    ]
    assert result["roles"] == {
        "INBOX": "inbox",
        SENT_MUTF7: "sent",
        "Archiv": "archive",
        ALL_MUTF7: "all",
        "Papelera": "trash",
        "Courrier indésirable": "junk",
        "Brouillons": "drafts",
        "Favoris": "flagged",
    }


def test_rest_list_resolves_sent_alias_before_select(monkeypatch, tmp_path):
    conn = FakeConn()
    monkeypatch.setattr(email_routes, "_start_poller", lambda: None)
    monkeypatch.setattr(email_routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(email_routes, "_imap_connect", lambda _account_id=None, owner="": conn)
    endpoint = _route_endpoint(email_routes.setup_email_routes(), "/api/email/list")

    result = asyncio.run(endpoint(
        folder="Sent",
        limit=1,
        offset=0,
        filter="all",
        from_addr=None,
        account_id="localized-list",
        has_attachments=0,
        cached_only=0,
        cache_bust="test",
        owner="owner",
    ))

    assert result["folder"] == SENT_MUTF7
    assert conn.selects == [(f'"{SENT_MUTF7}"', True)]


def test_ui_folder_logic_uses_server_roles_without_name_substrings():
    inbox = Path("static/js/emailInbox.js").read_text()
    library = Path("static/js/emailLibrary.js").read_text()

    assert "export function folderRole(folder, roles" in inbox
    assert "export function folderLabelKey(folder, roles" in inbox
    assert "Object.hasOwn(roles, raw)" in inbox
    assert "data.roles" in inbox
    assert "data.roles" in library
    assert "data-i18n" in inbox
    assert "ui.email.folder.scheduled" in library
    assert "folderRole(cardFolder, roles) === 'sent'" in library
    assert "/sent/i.test" not in library
    assert ".includes(String(p).toLowerCase())" not in library
    for key in (
        "ui.email.folder.inbox",
        "ui.email.folder.sent",
        "ui.email.folder.flagged",
        "ui.email.folder.all",
        "ui.email.folder.archive",
        "ui.email.folder.junk",
        "ui.email.folder.trash",
        "ui.email.folder.drafts",
    ):
        assert f"'{key}'" in inbox
