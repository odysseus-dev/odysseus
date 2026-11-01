"""Email list stale-while-revalidate (SWR) regression tests.

A plain list view must never block ~2-5 s on IMAP while we already hold a
recent snapshot or a durable index: it should return the snapshot immediately
(with ``sync.stale``) and refresh in the background. Blocking IMAP only remains
for a genuine cold start (no snapshot AND no indexed rows).
"""
import asyncio
import pathlib

from routes.email import email_routes as er

SRC = pathlib.Path(er.__file__).read_text(encoding="utf-8")


def _list_endpoint():
    router = er.setup_email_routes()
    for route in router.routes:
        if getattr(route, "name", "") == "list_emails":
            return route.endpoint
    raise AssertionError("list_emails route not found")


def _call(endpoint, **over):
    kwargs = dict(
        folder="INBOX", limit=50, offset=0, filter="all", from_addr=None,
        account_id=None, has_attachments=0, date_from=None, date_to=None,
        cached_only=0, cache_bust=None, refresh=0, owner="pewds",
    )
    kwargs.update(over)
    return asyncio.run(endpoint(**kwargs))


def test_list_returns_index_snapshot_with_a_stale_marker_without_blocking_on_imap(monkeypatch):
    endpoint = _list_endpoint()
    monkeypatch.setattr(
        er, "_email_index_list",
        lambda owner, account_id, folder, filter_, limit, offset, has_att: (
            [{"uid": "11", "subject": "Indexed", "is_read": False}], 1, "2026-10-11T00:00:00Z",
        ),
    )
    # The synchronous path must never open IMAP; if it did the request would
    # raise instead of returning the index snapshot immediately.
    monkeypatch.setattr(er, "_imap_connect", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("SWR must not open IMAP when an index snapshot exists")))

    result = _call(endpoint, cache_bust="123")
    assert result["total"] == 1
    assert [e["uid"] for e in result["emails"]] == ["11"]
    assert result["sync"]["stale"] is True
    assert result["sync"]["source"] in ("index", "cache_stale")


def test_cold_start_with_no_snapshot_and_no_index_is_not_marked_stale(monkeypatch):
    # With neither a snapshot nor an index, SWR must NOT fire: the request
    # falls through to the real (blocking) path, so the response is not a
    # stale-marked snapshot.
    endpoint = _list_endpoint()
    calls = []

    class EmptyMailbox:
        def select(self, folder, readonly=False):
            calls.append(("select", folder, readonly))
            return "OK", [b"0"]

        def uid(self, command, *args):
            assert command == "SEARCH"
            calls.append(("search", args))
            return "OK", [b""]

        def logout(self):
            pass

    def connect(*args, **kwargs):
        calls.append(("connect", args, kwargs))
        return EmptyMailbox()

    monkeypatch.setattr(er, "_start_poller", lambda: None)
    monkeypatch.setattr(er, "_email_index_list", lambda *a, **k: ([], 0, None))
    monkeypatch.setattr(er, "_imap_connect", connect)
    result = _call(endpoint)
    assert [call[0] for call in calls] == ["connect", "select", "search"]
    assert calls[1][2] is True
    assert isinstance(result, dict)
    assert result["emails"] == []
    assert result["total"] == 0
    assert not result.get("error")
    assert not (result.get("sync") or {}).get("stale")


def test_swr_contract_is_wired_into_the_list_path():
    assert "_LIST_STALE" in SRC
    assert "def _list_stale_put" in SRC
    assert "def _schedule_list_refresh" in SRC
    assert 'sync_meta["source"] = "cache_stale"' in SRC
    assert 'sync_meta["stale"] = True' in SRC
    # The manual-refresh contract the UI relies on must stay intact.
    assert "manual_refresh = bool(refresh)" in SRC
    assert "_invalidate_list_cache(account_id, folder)" in SRC
    assert "cached_rows = {} if refresh else _email_index_rows" in SRC


def test_frontend_email_library_recognizes_skip_fast_path():
    lib_path = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "emailLibrary" / "index.js"
    email_lib_src = lib_path.read_text(encoding="utf-8")
    assert "skipFastPath: true" in email_lib_src
    assert "skipFastPath = false" in email_lib_src
    assert "!skipFastPath" in email_lib_src


def test_manual_refresh_fetches_mailbox_instead_of_returning_stale_index(monkeypatch):
    class EmptyMailbox:
        def select(self, folder, readonly=False):
            return 'OK', [b'0']
        def uid(self, command, *args):
            assert command == 'SEARCH'
            return 'OK', [b'']
        def logout(self):
            pass

    endpoint = _list_endpoint()
    monkeypatch.setattr(er, '_start_poller', lambda: None)
    monkeypatch.setattr(er, '_imap_connect', lambda *a, **k: EmptyMailbox())
    monkeypatch.setattr(er, '_email_index_list', lambda *a, **k: (
        [{'uid': 'old', 'subject': 'Old'}], 1, 'yesterday'))

    result = _call(endpoint, refresh=1)
    assert result['emails'] == []
    assert result['total'] == 0
    assert not result.get('sync', {}).get('stale')


def test_stale_index_is_scoped_to_authenticated_owner(monkeypatch):
    endpoint = _list_endpoint()
    monkeypatch.setattr(er, '_start_poller', lambda: None)
    monkeypatch.setattr(er, '_email_index_list', lambda owner, *a: (
        [{'uid': owner, 'subject': owner}], 1, 'yesterday'))
    monkeypatch.setattr(er, '_imap_connect', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('offline')))

    alice = _call(endpoint, owner='alice')
    bob = _call(endpoint, owner='bob')
    assert [mail['uid'] for mail in alice['emails']] == ['alice']
    assert [mail['uid'] for mail in bob['emails']] == ['bob']


def test_inflight_refresh_cannot_restore_old_index_flags_after_mark_read(monkeypatch, tmp_path):
    """A delayed mailbox snapshot must not undo a completed owner mutation."""
    import threading
    from contextlib import contextmanager
    from routes.email import email_helpers

    database = tmp_path / 'email.db'
    monkeypatch.setattr(email_helpers, 'SCHEDULED_DB', database)
    monkeypatch.setattr(er, 'SCHEDULED_DB', database)
    email_helpers._init_scheduled_db()
    er._email_index_upsert('alice', 'acct', 'INBOX', [{
        'uid': '42', 'subject': 'Example', 'flags': '', 'date_epoch': 1,
    }])
    fetched = threading.Event()
    release = threading.Event()
    threads = []

    class Mailbox:
        def __init__(self, mutation=False):
            self.mutation = mutation
        def select(self, *args, **kwargs):
            return 'OK', [b'1']
        def uid(self, command, *args):
            if command == 'SEARCH':
                return 'OK', [b'42']
            if command == 'FETCH':
                if self.mutation:
                    return 'OK', [b'1 (UID 42)']
                threads.append(threading.current_thread())
                fetched.set()
                assert release.wait(5)
                return 'OK', [(b'1 (UID 42 FLAGS () RFC822.SIZE 100 RFC822.HEADER {20}',
                               b'Subject: Example\r\n\r\n')]
            if command == 'STORE':
                return 'OK', []
            raise AssertionError(command)
        def logout(self):
            pass

    @contextmanager
    def mailbox(*args, **kwargs):
        yield Mailbox(mutation=True)

    monkeypatch.setattr(er, '_start_poller', lambda: None)
    monkeypatch.setattr(er, '_imap_connect', lambda *a, **kw: Mailbox())
    monkeypatch.setattr(er, '_imap', mailbox)
    router = er.setup_email_routes()
    listing = next(r.endpoint for r in router.routes if getattr(r, 'name', '') == 'list_emails')
    mark_read = next(r.endpoint for r in router.routes if getattr(r, 'name', '') == 'mark_read')
    try:
        assert _call(listing, owner='alice', account_id='acct')['emails'][0]['is_read'] is False
        assert fetched.wait(5)
        refresh_thread = threads[0]
        result = asyncio.run(mark_read('42', folder='INBOX', account_id='acct', owner='alice'))
        assert result['success'] is True
    finally:
        release.set()
    refresh_thread.join(timeout=5)
    assert not refresh_thread.is_alive()
    rows, _, _ = er._email_index_list('alice', 'acct', 'INBOX', 'all', 50, 0)
    assert rows[0]['is_read'] is True


def test_mark_read_keeps_other_owners_default_mailbox_unread(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from routes.email import email_helpers

    database = tmp_path / 'email.db'
    monkeypatch.setattr(email_helpers, 'SCHEDULED_DB', database)
    monkeypatch.setattr(er, 'SCHEDULED_DB', database)
    email_helpers._init_scheduled_db()
    for owner in ('alice', 'bob'):
        er._email_index_upsert(owner, None, 'INBOX', [{
            'uid': '42', 'subject': owner, 'flags': '', 'date_epoch': 1,
        }])

    class Mailbox:
        def select(self, *args, **kwargs):
            return 'OK', [b'1']
        def uid(self, command, *args):
            if command == 'FETCH':
                return 'OK', [b'1 (UID 42)']
            if command == 'STORE':
                return 'OK', []
            raise AssertionError(command)

    @contextmanager
    def mailbox(*args, **kwargs):
        yield Mailbox()

    monkeypatch.setattr(er, '_start_poller', lambda: None)
    monkeypatch.setattr(er, '_imap', mailbox)
    monkeypatch.setattr(er, '_imap_connect', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    router = er.setup_email_routes()
    listing = next(r.endpoint for r in router.routes if getattr(r, 'name', '') == 'list_emails')
    mark_read = next(r.endpoint for r in router.routes if getattr(r, 'name', '') == 'mark_read')
    assert _call(listing, owner='bob')['emails'][0]['is_read'] is False
    assert asyncio.run(mark_read('42', folder='INBOX', account_id=None, owner='alice'))['success'] is True
    assert _call(listing, owner='bob')['emails'][0]['is_read'] is False
    rows, _, _ = er._email_index_list('alice', None, 'INBOX', 'all', 50, 0)
    assert rows[0]['is_read'] is True
