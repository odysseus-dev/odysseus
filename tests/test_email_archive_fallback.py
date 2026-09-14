from contextlib import contextmanager
from pathlib import Path
import re

import pytest


def _route_endpoint(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


def _fake_mailbox_name(mailbox):
    text = mailbox.decode(errors="replace") if isinstance(mailbox, bytes) else str(mailbox)
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1].replace(r"\"", '"').replace(r"\\", "\\")
    return text


def _fake_list_line_name(line):
    decoded = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
    match = re.search(r'"([^"]*)"\s*$|(\S+)\s*$', decoded)
    return (match.group(1) or match.group(2)) if match else ""


class FakeArchiveConn:
    def __init__(self, folders=None, create_status="OK", create_data=None):
        self.folders = list(folders or ['(\\HasNoChildren) "/" "INBOX"'])
        self.create_status = create_status
        self.create_data = create_data if create_data is not None else [b"CREATE completed"]
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return "OK", list(self.folders)

    def select(self, mailbox):
        self.calls.append(("select", mailbox))
        return "OK", [b"1"]

    def create(self, mailbox):
        self.calls.append(("create", mailbox))
        if self.create_status == "OK":
            self.folders.append(f'(\\HasNoChildren) "/" {mailbox}')
        return self.create_status, self.create_data

    def subscribe(self, mailbox):
        self.calls.append(("subscribe", mailbox))
        return "OK", [b"SUBSCRIBE completed"]


def test_find_mail_folder_detects_archive_candidates_and_special_use_flag():
    import routes.email_routes as email_routes

    special_use = FakeArchiveConn(folders=['(\\HasNoChildren \\Archive) "/" "Old Mail"'])
    gmail = FakeArchiveConn(folders=['(\\HasNoChildren) "/" "[Gmail]/All Mail"'])

    assert email_routes._find_mail_folder(special_use, "archive") == "Old Mail"
    assert email_routes._find_mail_folder(gmail, "archive") == "[Gmail]/All Mail"


def test_find_mail_folder_returns_none_when_archive_destination_is_missing():
    import routes.email_routes as email_routes

    conn = FakeArchiveConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Sent"',
        ]
    )

    assert email_routes._find_mail_folder(conn, "archive") is None


def test_create_archive_mail_folder_creates_and_subscribes_archive():
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    ok, result = email_routes._create_archive_mail_folder(conn)

    assert ok is True
    assert result == {"folder": "Archive", "created": True}
    assert ("create", '"Archive"') in conn.calls
    assert ("subscribe", '"Archive"') in conn.calls
    assert "Archive" in [email_routes._folder_name_from_list_line(f) for f in conn.folders]


def test_create_archive_mail_folder_rejects_non_archive_name():
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    ok, result = email_routes._create_archive_mail_folder(conn, "Trash")

    assert ok is False
    assert result["error"] == "Archive setup can only create a folder named Archive"
    assert not any(call[0] == "create" for call in conn.calls)


def test_archive_route_returns_setup_response_when_archive_folder_missing(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    move_calls = []
    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    monkeypatch.setattr(email_routes, "_move_email_message", lambda *args, **kwargs: move_calls.append((args, kwargs)) or True)
    router = email_routes.setup_email_routes()
    archive_email = _route_endpoint(router, "/api/email/archive/{uid}", "POST")

    result = archive_email("42", folder="INBOX", account_id="acct-alice", owner="alice")

    assert result == {
        "success": False,
        "needs_archive_folder": True,
        "suggested_folder": "Archive",
        "error": "No archive folder found",
    }
    assert move_calls == []


def test_archive_route_uses_existing_archive_folder(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeArchiveConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Archive"',
        ]
    )
    move_calls = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    def fake_move(conn_arg, uid, dest, role=""):
        move_calls.append((uid, dest, role))
        return True

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    monkeypatch.setattr(email_routes, "_move_email_message", fake_move)
    router = email_routes.setup_email_routes()
    archive_email = _route_endpoint(router, "/api/email/archive/{uid}", "POST")

    result = archive_email("42", folder="INBOX", account_id="acct-alice", owner="alice")

    assert result == {"success": True, "folder": "Archive"}
    assert move_calls == [("42", "Archive", "archive")]


@pytest.mark.asyncio
async def test_create_archive_folder_route_returns_updated_folders(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    create_archive_folder = _route_endpoint(router, "/api/email/archive-folder", "POST")

    result = await create_archive_folder({"name": "Archive"}, account_id="acct-alice", owner="alice")

    assert result["success"] is True
    assert result["folder"] == "Archive"
    assert result["created"] is True
    assert result["folders"] == ["INBOX", "Archive"]


@pytest.mark.asyncio
async def test_create_archive_folder_invalidates_stale_folder_cache(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    list_folders = _route_endpoint(router, "/api/email/folders", "GET")
    create_archive_folder = _route_endpoint(router, "/api/email/archive-folder", "POST")

    before = await list_folders(account_id="acct-alice", owner="alice")
    assert before["folders"] == ["INBOX"]

    result = await create_archive_folder({"name": "Archive"}, account_id="acct-alice", owner="alice")
    assert result["success"] is True

    after = await list_folders(account_id="acct-alice", owner="alice")
    assert after["sync"]["source"] == "imap"
    assert after["folders"] == ["INBOX", "Archive"]


@pytest.mark.asyncio
async def test_list_folders_refresh_bypasses_stale_folder_cache(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeArchiveConn()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    list_folders = _route_endpoint(router, "/api/email/folders", "GET")

    before = await list_folders(account_id="acct-alice", refresh=False, owner="alice")
    assert before["folders"] == ["INBOX"]

    conn.folders.append('(\\HasNoChildren) "/" "Archive"')

    cached = await list_folders(account_id="acct-alice", refresh=False, owner="alice")
    assert cached["sync"]["source"] == "folder_cache"
    assert cached["folders"] == ["INBOX"]

    refreshed = await list_folders(account_id="acct-alice", refresh=True, owner="alice")
    assert refreshed["sync"]["source"] == "imap"
    assert refreshed["folders"] == ["INBOX", "Archive"]


def test_email_library_handles_archive_folder_setup_flow():
    src = Path("static/js/emailLibrary.js").read_text(encoding="utf-8")
    helper = Path("static/js/emailArchiveFallback.js").read_text(encoding="utf-8")

    assert "import { folderDisplayName, isArchiveFolder, sortedFolders } from './emailInbox.js" in src
    assert "import { captureArchiveAction, runArchiveFallback } from './emailArchiveFallback.js'" in src
    assert "_archiveEmailWithFallback" in src
    assert "needs_archive_folder" in helper
    assert "No Archive folder was found for this account. Create one named" in src
    assert "/api/email/archive-folder" in src
    assert "successfulArchiveUids" in src
    assert "const result = await _archiveEmailWithFallback(em.uid)" in src
    assert "actions.findIndex(a => a.label === 'Move to Archive')" in src
    assert "const isArchiveCurrentFolder = isArchiveFolder(state._libFolder)" in src
    assert "cached_only: (live || refresh) ? undefined : 1" in src
    assert "refresh: refresh ? 1 : undefined" in src
    assert "_loadFolders({ refresh: true })" in src
    assert "_loadFolders({ resetMissing: true, refresh: true })" in src
    assert "account_id: captured.accountId || undefined" in src
    assert "_loadFolders({ accountId: captured.accountId, refresh: true })" in src


def test_sidebar_inbox_archive_uses_fallback_before_local_removal():
    src = Path("static/js/emailInbox.js").read_text(encoding="utf-8")
    helper = Path("static/js/emailArchiveFallback.js").read_text(encoding="utf-8")

    assert "export function isArchiveFolder(folder)" in src
    assert "_archiveEmailWithFallback" in src
    assert "import { captureArchiveAction, runArchiveFallback } from './emailArchiveFallback.js'" in src
    assert "needs_archive_folder" in helper
    assert "/api/email/archive-folder" in src
    assert "if (!isArchiveFolder(_currentFolder))" in src
    assert "'ontouchstart' in window && !isArchiveFolder(_currentFolder)" in src
    assert "if (!result.success)" in src
    assert "_restoreArchiveSwipeItem(itemEl)" in src
    assert "loadFolders({ accountId: captured.accountId, refresh: true })" in src
    assert "await fetch(`${API_BASE}/api/email/archive/${em.uid}?folder=${encodeURIComponent(_currentFolder)}${_acct()}`, { method: 'POST' });\n    _emails = _emails.filter" not in src
