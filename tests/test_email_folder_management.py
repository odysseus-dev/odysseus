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


class FakeCreateFolderConn:
    def __init__(
        self,
        folders=None,
        create_status="OK",
        create_data=None,
        rename_status="OK",
        rename_data=None,
        delete_status="OK",
        delete_data=None,
        message_counts=None,
        message_count_sequences=None,
        utf8_enabled=False,
        capabilities=(),
    ):
        self.folders = list(folders or ['(\\HasNoChildren) "/" "INBOX"'])
        self.create_status = create_status
        self.create_data = create_data if create_data is not None else [b"CREATE completed"]
        self.rename_status = rename_status
        self.rename_data = rename_data if rename_data is not None else [b"RENAME completed"]
        self.delete_status = delete_status
        self.delete_data = delete_data if delete_data is not None else [b"DELETE completed"]
        self.message_counts = {str(k).casefold(): int(v) for k, v in (message_counts or {}).items()}
        self.message_count_sequences = {
            str(k).casefold(): [int(value) for value in values]
            for k, values in (message_count_sequences or {}).items()
        }
        self.utf8_enabled = utf8_enabled
        self.capabilities = capabilities
        self.calls = []

    def list(self):
        self.calls.append(("list",))
        return "OK", list(self.folders)

    def create(self, mailbox):
        self.calls.append(("create", mailbox))
        if self.create_status == "OK":
            self.folders.append(f'(\\HasNoChildren) "/" {mailbox}')
        return self.create_status, self.create_data

    def subscribe(self, mailbox):
        self.calls.append(("subscribe", mailbox))
        return "OK", [b"SUBSCRIBE completed"]

    def rename(self, old_mailbox, new_mailbox):
        self.calls.append(("rename", old_mailbox, new_mailbox))
        if self.rename_status == "OK":
            old_name = _fake_mailbox_name(old_mailbox).casefold()
            new_line = f'(\\HasNoChildren) "/" {new_mailbox}'
            self.folders = [
                new_line if _fake_list_line_name(line).casefold() == old_name else line
                for line in self.folders
            ]
        return self.rename_status, self.rename_data

    def status(self, mailbox, items):
        self.calls.append(("status", mailbox, items))
        folder = _fake_mailbox_name(mailbox)
        sequence = self.message_count_sequences.get(folder.casefold())
        if sequence:
            count = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        else:
            count = self.message_counts.get(folder.casefold(), 0)
        return "OK", [f"{mailbox} (MESSAGES {count})".encode()]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        folder = _fake_mailbox_name(mailbox)
        count = self.message_counts.get(folder.casefold(), 0)
        return "OK", [str(count).encode()]

    def close(self):
        self.calls.append(("close",))
        return "OK", [b"CLOSE completed"]

    def delete(self, mailbox):
        self.calls.append(("delete", mailbox))
        if self.delete_status == "OK":
            target = _fake_mailbox_name(mailbox).casefold()
            self.folders = [
                line for line in self.folders
                if _fake_list_line_name(line).casefold() != target
            ]
        return self.delete_status, self.delete_data


@pytest.mark.parametrize(
    ("name", "wire_name"),
    [
        ("Résumé", "R&AOk-sum&AOk-"),
        ("台北", "&U,BTFw-"),
        ("R&D", "R&-D"),
        ("Plain ASCII", "Plain ASCII"),
    ],
)
def test_modified_utf7_known_mailbox_names_round_trip(name, wire_name):
    import routes.email_helpers as email_helpers

    assert email_helpers._imap_modified_utf7_encode(name) == wire_name
    assert email_helpers._imap_modified_utf7_decode(wire_name) == name


def test_create_imap_folder_quotes_and_subscribes():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn()

    ok, folder = email_routes._create_imap_folder(conn, "Project Mail")

    assert ok is True
    assert folder == "Project Mail"
    assert ("create", '"Project Mail"') in conn.calls
    assert ("subscribe", '"Project Mail"') in conn.calls


def test_create_imap_folder_encodes_unicode_with_modified_utf7():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(capabilities=("IMAP4REV1", "UTF8=ACCEPT"))

    ok, folder = email_routes._create_imap_folder(conn, "Résumé")

    assert ok is True
    assert folder == "Résumé"
    assert ("create", '"R&AOk-sum&AOk-"') in conn.calls
    assert ("subscribe", '"R&AOk-sum&AOk-"') in conn.calls
    assert email_routes._list_imap_folders(conn)[1] == ["INBOX", "Résumé"]


def test_create_imap_folder_uses_unicode_only_after_utf8_accept_is_negotiated():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(utf8_enabled=True, capabilities=("IMAP4REV1", "UTF8=ACCEPT"))

    ok, folder = email_routes._create_imap_folder(conn, "Résumé")

    assert ok is True
    assert folder == "Résumé"
    assert ("create", '"Résumé"') in conn.calls
    assert ("subscribe", '"Résumé"') in conn.calls


def test_create_imap_folder_rejects_duplicate_without_create_call():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Project Mail"',
        ]
    )

    ok, error = email_routes._create_imap_folder(conn, "project mail")

    assert ok is False
    assert error == "Folder already exists"
    assert not any(call[0] == "create" for call in conn.calls)


@pytest.mark.parametrize("name", ["", "   ", "bad\nname", "bad\rname", "bad\x00name", "__scheduled__", "__SCHEDULED__", "INBOX"])
def test_create_imap_folder_rejects_invalid_names(name):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn()

    ok, error = email_routes._create_imap_folder(conn, name)

    assert ok is False
    assert error
    assert not any(call[0] == "create" for call in conn.calls)


def test_rename_imap_folder_quotes_and_subscribes_new_folder():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Project Mail"',
        ]
    )

    ok, result = email_routes._rename_imap_folder(conn, "project mail", "Client Mail")

    assert ok is True
    assert result == {"old_folder": "Project Mail", "folder": "Client Mail"}
    assert ("rename", '"Project Mail"', '"Client Mail"') in conn.calls
    assert ("subscribe", '"Client Mail"') in conn.calls
    assert "Client Mail" in [email_routes._folder_name_from_list_line(f) for f in conn.folders]


def test_rename_imap_folder_encodes_unicode_with_modified_utf7():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "R&AOk-sum&AOk-"',
        ]
    )

    ok, result = email_routes._rename_imap_folder(conn, "Résumé", "台北")

    assert ok is True
    assert result == {"old_folder": "Résumé", "folder": "台北"}
    assert ("rename", '"R&AOk-sum&AOk-"', '"&U,BTFw-"') in conn.calls
    assert ("subscribe", '"&U,BTFw-"') in conn.calls
    assert email_routes._list_imap_folders(conn)[1] == ["INBOX", "台北"]


def test_rename_imap_folder_rejects_duplicate_without_rename_call():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Project Mail"',
            '(\\HasNoChildren) "/" "Client Mail"',
        ]
    )

    ok, result = email_routes._rename_imap_folder(conn, "Project Mail", "client mail")

    assert ok is False
    assert result["error"] == "Folder already exists"
    assert not any(call[0] == "rename" for call in conn.calls)


def test_rename_imap_folder_rejects_protected_source_but_allows_presentation_name():
    import routes.email_routes as email_routes

    protected = FakeCreateFolderConn(folders=['(\\HasNoChildren) "/" "INBOX"'])

    ok, result = email_routes._rename_imap_folder(protected, "INBOX", "Clients")

    assert ok is False
    assert result["error"]
    assert not any(call[0] == "rename" for call in protected.calls)

    custom = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Presentation"',
        ]
    )

    ok, result = email_routes._rename_imap_folder(custom, "Presentation", "Presentation Notes")

    assert ok is True
    assert result["folder"] == "Presentation Notes"


def test_delete_imap_folder_deletes_empty_custom_folder():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Project Mail"',
        ],
        message_counts={"Project Mail": 0},
    )

    ok, result = email_routes._delete_imap_folder(conn, "project mail", confirm_delete=True)

    assert ok is True
    assert result["folder"] == "Project Mail"
    assert result["message_count"] == 0
    assert ("delete", '"Project Mail"') in conn.calls
    assert "Project Mail" not in [email_routes._folder_name_from_list_line(f) for f in conn.folders]


def test_delete_imap_folder_requires_explicit_destructive_confirmation():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_counts={"Clients": 0},
    )

    ok, result = email_routes._delete_imap_folder(conn, "Clients")

    assert ok is False
    assert result["needs_confirmation"] is True
    assert result["confirmation_kind"] == "delete"
    assert not any(call[0] == "delete" for call in conn.calls)


def test_delete_imap_folder_fails_closed_when_delivery_arrives_between_checks():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_count_sequences={"Clients": [0, 1]},
    )

    ok, result = email_routes._delete_imap_folder(conn, "Clients", confirm_delete=True)

    assert ok is False
    assert result["needs_confirmation"] is True
    assert result["confirmation_kind"] == "nonempty"
    assert result["message_count"] == 1
    assert len([call for call in conn.calls if call[0] == "status"]) == 2
    assert not any(call[0] == "delete" for call in conn.calls)


def test_delete_imap_folder_encodes_unicode_with_modified_utf7():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "&U,BTFw-"',
        ],
        message_counts={"&U,BTFw-": 0},
    )

    ok, result = email_routes._delete_imap_folder(conn, "台北", confirm_delete=True)

    assert ok is True
    assert result == {"folder": "台北", "message_count": 0}
    assert ("delete", '"&U,BTFw-"') in conn.calls


def test_delete_imap_folder_requires_confirmation_for_nonempty_folder():
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_counts={"Clients": 2},
    )

    ok, result = email_routes._delete_imap_folder(conn, "Clients", confirm_delete=True)

    assert ok is False
    assert result["needs_confirmation"] is True
    assert result["message_count"] == 2
    assert not any(call[0] == "delete" for call in conn.calls)

    ok, result = email_routes._delete_imap_folder(
        conn,
        "Clients",
        confirm_delete=True,
        confirm_nonempty=True,
    )

    assert ok is True
    assert result["folder"] == "Clients"
    assert ("delete", '"Clients"') in conn.calls


@pytest.mark.parametrize(
    "name,line",
    [
        ("INBOX", '(\\HasNoChildren) "/" "INBOX"'),
        ("Sent", '(\\Sent \\HasNoChildren) "/" "Sent"'),
        ("Drafts", '(\\Drafts \\HasNoChildren) "/" "Drafts"'),
        ("Archive", '(\\Archive \\HasNoChildren) "/" "Archive"'),
        ("Spam", '(\\Junk \\HasNoChildren) "/" "Spam"'),
        ("Trash", '(\\Trash \\HasNoChildren) "/" "Trash"'),
    ],
)
def test_delete_imap_folder_rejects_protected_folders(name, line):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(folders=[line])

    ok, result = email_routes._delete_imap_folder(
        conn,
        name,
        confirm_delete=True,
        confirm_nonempty=True,
    )

    assert ok is False
    assert result["error"]
    assert not any(call[0] == "delete" for call in conn.calls)


@pytest.mark.asyncio
async def test_create_folder_route_creates_and_returns_updated_list(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn()
    seen = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        seen.append((account_id, owner))
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    create_folder = _route_endpoint(router, "/api/email/folders", "POST")

    result = await create_folder({"name": "Clients"}, account_id="acct-alice", owner="alice")

    assert result["success"] is True
    assert result["folder"] == "Clients"
    assert result["folders"] == ["INBOX", "Clients"]
    assert seen == [("acct-alice", "alice")]
    assert ("create", '"Clients"') in conn.calls


@pytest.mark.asyncio
async def test_create_folder_route_invalidates_stale_folder_cache(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn()

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    list_folders = _route_endpoint(router, "/api/email/folders", "GET")
    create_folder = _route_endpoint(router, "/api/email/folders", "POST")

    before = await list_folders(account_id="acct-alice", owner="alice")
    assert before["folders"] == ["INBOX"]

    result = await create_folder({"name": "Clients"}, account_id="acct-alice", owner="alice")
    assert result["success"] is True

    after = await list_folders(account_id="acct-alice", owner="alice")
    assert after["sync"]["source"] == "imap"
    assert after["folders"] == ["INBOX", "Clients"]


@pytest.mark.asyncio
async def test_folder_status_route_returns_message_count(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_counts={"Clients": 3},
    )

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    folder_status = _route_endpoint(router, "/api/email/folders/status", "GET")

    result = await folder_status(folder="Clients", account_id="acct-alice", owner="alice")

    assert result == {"success": True, "folder": "Clients", "message_count": 3}
    assert ("status", '"Clients"', "(MESSAGES)") in conn.calls


@pytest.mark.asyncio
async def test_rename_folder_route_renames_and_returns_updated_list(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ]
    )
    seen = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        seen.append((account_id, owner))
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    rename_folder = _route_endpoint(router, "/api/email/folders", "PATCH")

    result = await rename_folder({"folder": "Clients", "name": "Customers"}, account_id="acct-alice", owner="alice")

    assert result["success"] is True
    assert result["old_folder"] == "Clients"
    assert result["folder"] == "Customers"
    assert result["folders"] == ["INBOX", "Customers"]
    assert seen == [("acct-alice", "alice")]
    assert ("rename", '"Clients"', '"Customers"') in conn.calls


@pytest.mark.asyncio
async def test_delete_folder_route_deletes_and_returns_updated_list(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_counts={"Clients": 0},
    )
    seen = []

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        seen.append((account_id, owner))
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    delete_folder = _route_endpoint(router, "/api/email/folders", "DELETE")

    result = await delete_folder(
        folder="Clients",
        confirm_delete=True,
        confirm_nonempty=False,
        account_id="acct-alice",
        owner="alice",
    )

    assert result["success"] is True
    assert result["folder"] == "Clients"
    assert result["folders"] == ["INBOX"]
    assert seen == [("acct-alice", "alice")]
    assert ("delete", '"Clients"') in conn.calls


@pytest.mark.asyncio
async def test_delete_folder_route_requires_nonempty_confirmation(monkeypatch):
    import routes.email_routes as email_routes

    conn = FakeCreateFolderConn(
        folders=[
            '(\\HasNoChildren) "/" "INBOX"',
            '(\\HasNoChildren) "/" "Clients"',
        ],
        message_counts={"Clients": 1},
    )

    @contextmanager
    def fake_imap(account_id=None, owner=""):
        yield conn

    monkeypatch.setattr(email_routes, "_imap", fake_imap)
    router = email_routes.setup_email_routes()
    delete_folder = _route_endpoint(router, "/api/email/folders", "DELETE")

    result = await delete_folder(
        folder="Clients",
        confirm_delete=True,
        confirm_nonempty=False,
        account_id="acct-alice",
        owner="alice",
    )

    assert result["success"] is False
    assert result["needs_confirmation"] is True
    assert result["message_count"] == 1
    assert not any(call[0] == "delete" for call in conn.calls)


def test_email_library_exposes_folder_creation_control():
    src = Path("static/js/emailLibrary.js").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")

    assert "email-lib-new-folder-btn" in src
    assert "styledPrompt('Folder name'" in src
    assert "/api/email/folders${_acctStart()}" in src
    assert "method: 'POST'" in src
    assert "email-lib-delete-folder-btn" in src
    assert "email-lib-rename-folder-btn" in src
    assert 'id="email-lib-rename-folder-btn" title="Rename folder" aria-label="Rename selected folder" disabled' in src
    assert 'id="email-lib-delete-folder-btn" title="Delete folder" aria-label="Delete selected folder" disabled' in src
    assert "_isCustomEmailFolder" in src
    assert "_emailFolderApiUrl('/status'" in src
    assert "method: 'PATCH'" in src
    assert "method: 'DELETE'" in src
    assert "confirm_delete: 'true'" in src
    assert "PERMANENTLY DELETE folder" in src
    assert "New mail can arrive before deletion" in src
    assert "permanently delete every message inside it" in src
    assert src.index("schedOpt.value = '__scheduled__'") < src.index("for (const f of others)")
    assert ".email-folder-create-btn" in css
    assert ".email-folder-rename-btn" in css
    assert ".email-folder-delete-btn" in css
