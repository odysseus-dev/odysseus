"""Regression: `odysseus-mail list` must return messages newest-first by DATE.

cmd_list took the highest UIDs (`reversed(all_uids)[:limit]`) and emitted them
in that order, never sorting by the parsed Date header — despite the "Newest
first" comment. IMAP UID/arrival order is not Date order (delayed delivery,
APPEND/migration of older mail, moved messages), so a message that arrived
later but has an older Date sorted above a newer one. The web route
(routes/email_routes._list_emails_sync) sorts by the parsed UTC epoch; the CLI
must do the same.
"""
import importlib.machinery
import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _raw(subject, frm, date):
    return (f"Subject: {subject}\r\nFrom: {frm}\r\nDate: {date}\r\n\r\n").encode()


class FakeConn:
    # UID 1 has the NEWER date; UID 2 (higher UID = "arrived later") is OLDER.
    _HEADERS = {
        b"1": _raw("Newer", "a@x.com", "Wed, 03 Jun 2026 12:00:00 +0000"),
        b"2": _raw("Older", "b@x.com", "Mon, 01 Jun 2026 09:00:00 +0000"),
    }

    def select(self, folder, readonly=True):
        return "OK", [b"2"]

    def search(self, charset, query):
        return "OK", [b"1 2"]

    def fetch(self, uid, spec):
        meta = b"%s (FLAGS () RFC822.HEADER {})" % uid
        return "OK", [(meta, self._HEADERS[uid])]


def _load_cli(monkeypatch):
    @contextmanager
    def _imap(account):
        yield FakeConn()

    helpers = types.ModuleType("routes.email_helpers")
    helpers._imap = _imap
    helpers._get_email_config = lambda *a, **k: {}
    helpers._decode_header = lambda v: v
    helpers._extract_text = lambda *a, **k: ""
    helpers._extract_html = lambda *a, **k: ""
    helpers._list_attachments_from_msg = lambda *a, **k: []
    pollers = types.ModuleType("routes.email_pollers")
    pollers._scheduled_poll_once = lambda *a, **k: None
    pollers._run_auto_summarize_once = lambda *a, **k: None
    db = types.ModuleType("core.database")
    db.SessionLocal = lambda: None
    db.EmailAccount = object

    routes_pkg = sys.modules.get("routes") or types.ModuleType("routes")
    core_pkg = sys.modules.get("core") or types.ModuleType("core")
    monkeypatch.setitem(sys.modules, "routes", routes_pkg)
    monkeypatch.setitem(sys.modules, "routes.email_helpers", helpers)
    monkeypatch.setitem(sys.modules, "routes.email_pollers", pollers)
    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.database", db)

    path = ROOT / "scripts" / "odysseus-mail"
    loader = importlib.machinery.SourceFileLoader("odysseus_mail_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_list_is_sorted_newest_first_by_date(monkeypatch):
    cli = _load_cli(monkeypatch)

    captured = {}
    cli.emit = lambda payload, args: captured.setdefault("rows", payload)

    args = types.SimpleNamespace(account=None, folder="INBOX", limit=10, json=False)
    cli.cmd_list(args)

    rows = captured["rows"]
    subjects = [r["subject"] for r in rows]
    # The Jun-03 message (UID 1) must come before the Jun-01 message (UID 2),
    # even though UID 2 is higher (arrived later). On the old code the
    # reversed-UID order put "Older" (UID 2) first.
    assert subjects == ["Newer", "Older"], subjects
