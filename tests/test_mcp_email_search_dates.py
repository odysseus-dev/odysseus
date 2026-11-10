"""Date refinement through the public MCP contract, without live mailboxes."""
import asyncio
from datetime import datetime
import re
import sqlite3

import pytest
from mcp_servers import email_server as es


@pytest.fixture
def indexed_mailbox(monkeypatch, tmp_path):
    monkeypatch.setenv('ODYSSEUS_EMAIL_FIXTURE', '0')
    monkeypatch.setenv('EMAIL_CACHE_DB', str(tmp_path / 'missing-cache.db'))
    monkeypatch.setattr(es, '_ACCOUNT_CACHE', {})
    account_defaults = dict.fromkeys((
        'imap_host', 'imap_port', 'imap_password', 'imap_starttls',
        'smtp_host', 'smtp_port', 'smtp_security', 'smtp_user', 'smtp_password', 'from_address'), '')
    monkeypatch.setattr(es, '_read_accounts_from_db', lambda: [
        {**account_defaults, 'id': 'account-a', 'owner': 'alice', 'name': 'Test', 'imap_user': 'alice@example.invalid'},
        {**account_defaults, 'id': 'account-b', 'owner': 'bob', 'name': 'Other', 'imap_user': 'bob@example.invalid'},
    ])
    db = tmp_path / 'index.db'
    monkeypatch.setattr(es, 'SCHEDULED_EMAILS_DB', str(db))
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE email_message_index (
            owner TEXT, account_key TEXT, folder TEXT, uid TEXT, message_id TEXT,
            subject TEXT, from_name TEXT, from_address TEXT, to_text TEXT,
            cc_text TEXT, date_iso TEXT, date_display TEXT, date_epoch REAL)''')
        for owner, account, uid, date in [
            ('alice', 'account-a', '10', '2026-06-30T23:59:59+00:00'),
            ('alice', 'account-a', '20', '2026-07-01T00:00:00+00:00'),
            ('alice', 'account-a', '30', '2026-08-01T00:00:00+00:00'),
            ('bob', 'account-b', '99', '2026-07-02T00:00:00+00:00'),
        ]:
            conn.execute('INSERT INTO email_message_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (owner, account, 'INBOX', uid, f'<{uid}@example.invalid>',
                 'Fixture receipt', 'Fixture', 'fixture@example.invalid', '', '',
                 date, date, datetime.fromisoformat(date).timestamp()))
    def no_network(*args, **kwargs):
        raise AssertionError('This index test must not access a real mailbox')
    monkeypatch.setattr(es, '_imap_connect', no_network)
    return db


def search(**kwargs):
    result = asyncio.run(es.call_tool('search_emails', {
        '_odysseus_owner': 'alice', 'query': 'receipt', 'folders': ['INBOX'],
        'max_results': 1, **kwargs,
    }))
    return '\n'.join(item.text for item in result)


def uids(text):
    return re.findall(r'^\s*UID:\s*(\S+)', text, re.M)


def test_list_date_refinement_filters_imap_before_limit(indexed_mailbox, monkeypatch):
    monkeypatch.setattr(es, 'SCHEDULED_EMAILS_DB', indexed_mailbox.with_name('missing.db'))
    dates = {b'10': 'Tue, 30 Jun 2026 23:59:59 +0000',
             b'20': 'Wed, 01 Jul 2026 09:00:00 +0900',
             b'30': 'Sat, 01 Aug 2026 09:00:00 +0900'}
    class Mailbox:
        def select(self, folder, readonly=False):
            assert readonly
            return 'OK', []
        def uid(self, operation, *args):
            if operation == 'SEARCH':
                return 'OK', [b'10 20 30']
            assert operation == 'FETCH'
            return 'OK', [(b'1 (UID ' + uid + b' RFC822.HEADER {100}',
                f'Subject: Fixture receipt\r\nFrom: fixture@example.invalid\r\nDate: {dates[uid]}\r\n\r\n'.encode())
                for uid in args[0].split(b',')]
        def logout(self):
            pass
    monkeypatch.setattr(es, '_imap_connect', lambda account=None: Mailbox())
    result = asyncio.run(es.call_tool('list_emails', {
        '_odysseus_owner': 'alice', 'account': 'account-a', 'max_results': 1,
        'date_from': '2026-07-01T00:00:00Z', 'date_to': '2026-08-01T00:00:00Z',
    }))
    text = '\n'.join(item.text for item in result)
    assert uids(text) == ['20'], text


def test_list_date_refinement_pages_headers_and_preserves_unread_filter(indexed_mailbox, monkeypatch):
    monkeypatch.setattr(es, 'SCHEDULED_EMAILS_DB', indexed_mailbox.with_name('missing.db'))
    batches = []
    searches = []
    class Mailbox:
        def select(self, folder, readonly=False):
            assert readonly
            return 'OK', []
        def uid(self, operation, *args):
            if operation == 'SEARCH':
                searches.append(args[1])
                return 'OK', [b' '.join(str(n).encode() for n in range(1, 121))]
            assert operation == 'FETCH'
            ids = args[0].split(b',')
            batches.append(ids)
            return 'OK', [(b'1 (UID ' + uid + b' RFC822.HEADER {100}',
                ('Subject: Fixture\r\nDate: ' + ('unknown' if int(uid) > 110 else
                 'Sat, 01 Aug 2026 09:00:00 +0900' if int(uid) > 90 else
                 'Wed, 01 Jul 2026 09:00:00 +0900') + '\r\n\r\n').encode()) for uid in ids]
        def logout(self):
            pass
    monkeypatch.setattr(es, '_imap_connect', lambda account=None: Mailbox())
    result = asyncio.run(es.call_tool('list_emails', {
        '_odysseus_owner': 'alice', 'account': 'account-a', 'max_results': 60,
        'unread_only': True, 'date_from': '2026-07-01', 'date_to': '2026-08-01',
    }))
    text = '\n'.join(item.text for item in result)
    assert uids(text) == [str(n) for n in range(90, 30, -1)], text
    assert max(map(len, batches)) <= 50
    assert len(batches) == 2, 'Stop fetching once enough matching rows have been found'
    assert 'UNSEEN' in searches[0]
    assert 'SENTSINCE' in searches[0] and 'SENTBEFORE' in searches[0]


def test_indexed_listing_uses_utc_for_timezone_less_bounds(indexed_mailbox, monkeypatch):
    import time
    if not hasattr(time, 'tzset'):
        pytest.skip('Process timezone test requires tzset')
    with sqlite3.connect(indexed_mailbox) as conn:
        conn.execute('ALTER TABLE email_message_index ADD COLUMN attachment_names TEXT')
    try:
        with monkeypatch.context() as env:
            env.setenv('TZ', 'Pacific/Honolulu')
            time.tzset()
            result = asyncio.run(es.call_tool('list_emails', {
                '_odysseus_owner': 'alice', 'account': 'account-a', 'max_results': 1,
                'date_from': '2026-07-01', 'date_to': '2026-08-01',
            }))
    finally:
        time.tzset()
    text = '\n'.join(item.text for item in result)
    assert uids(text) == ['20'], text


@pytest.mark.parametrize('bounds', [
    {'date_from': 'not-a-date'}, {'date_to': '2026-99-99'},
    {'date_from': '2026-08-01', 'date_to': '2026-07-01'},
    {'date_from': '2026-07-01', 'date_to': '2026-07-01'},
])
def test_invalid_list_bounds_do_not_broaden_to_the_whole_mailbox(indexed_mailbox, bounds):
    result = asyncio.run(es.call_tool('list_emails', {
        '_odysseus_owner': 'alice', 'account': 'account-a', **bounds,
    }))
    text = '\n'.join(item.text for item in result)
    assert text.startswith('Error: date_'), text
    assert not uids(text)


def test_invalid_multi_account_list_bounds_are_rejected_before_scanning(indexed_mailbox, monkeypatch):
    accounts = es._read_accounts_from_db()
    monkeypatch.setattr(es, '_read_accounts_from_db', lambda: [
        {**row, 'owner': 'alice'} for row in accounts
    ])
    result = asyncio.run(es.call_tool('list_emails', {
        '_odysseus_owner': 'alice', 'date_from': 'not-a-date',
    }))
    text = '\n'.join(item.text for item in result)
    assert text.startswith('Error: date_from'), text
    assert not uids(text)


def test_date_filtered_listing_reuses_indexed_headers_after_imap_search(indexed_mailbox, monkeypatch):
    with sqlite3.connect(indexed_mailbox) as conn:
        conn.execute('ALTER TABLE email_message_index ADD COLUMN attachment_names TEXT')
    # The index lacks flags, so it cannot establish unread state. Only the live
    # IMAP search can select unread UIDs; their cached headers can still be reused.
    class Mailbox:
        def select(self, folder, readonly=False):
            assert readonly
            return 'OK', []
        def uid(self, operation, *args):
            assert operation == 'SEARCH', 'Already indexed headers must not be fetched again'
            assert 'UNSEEN' in args[1]
            return 'OK', [b'10 20 30']
        def logout(self):
            pass
    monkeypatch.setattr(es, '_imap_connect', lambda account=None: Mailbox())
    result = asyncio.run(es.call_tool('list_emails', {
        '_odysseus_owner': 'alice', 'account': 'account-a', 'unread_only': True,
        'max_results': 1, 'date_from': '2026-07-01', 'date_to': '2026-08-01',
    }))
    text = '\n'.join(item.text for item in result)
    assert uids(text) == ['20'], text


def test_search_date_refinement_filters_index_before_limit(indexed_mailbox):
    broad = search()
    assert uids(broad) == ['30'], broad
    assert uids(search(date_from='2026-07-01', date_to='2026-08-01')) == ['20']


@pytest.mark.parametrize('folder', ['INBOX', 'Archive'])
def test_missing_email_read_identifies_the_selected_folder(indexed_mailbox, monkeypatch, folder):
    class Mailbox:
        def select(self, selected, readonly=False):
            assert readonly
            assert folder in selected
            return 'OK', []
        def uid(self, operation, *args):
            assert operation == 'FETCH'
            assert args[1] == '(BODY.PEEK[])'
            return 'OK', [None]
        def logout(self):
            pass
    monkeypatch.setattr(es, '_imap_connect', lambda account=None: Mailbox())
    result = asyncio.run(es.call_tool('read_email', {
        '_odysseus_owner': 'alice', 'uid': '20', 'folder': folder,
    }))
    text = '\n'.join(item.text for item in result)
    assert 'Error: Email not found with UID 20' in text
    assert f'in folder {folder}' in text
    assert 'UIDs are folder-specific' in text


@pytest.mark.parametrize('bounds,expected', [
    ({'date_from': '2026-07-01'}, ['30', '20']),
    ({'date_to': '2026-08-01'}, ['20', '10']),
    ({'date_from': '2026-07-01T09:00:00+09:00', 'date_to': '2026-07-02T09:00:00+09:00'}, ['20']),
])
def test_index_search_bounds_preserve_exact_instants_and_owner(indexed_mailbox, bounds, expected):
    assert uids(search(max_results=10, **bounds)) == expected


@pytest.mark.parametrize('bounds', [
    {'date_from': 'not-a-date'}, {'date_to': '2026-99-99'},
    {'date_from': '2026-08-01', 'date_to': '2026-07-01'},
    {'date_from': '2026-07-01', 'date_to': '2026-07-01'},
])
def test_invalid_search_bounds_fail_instead_of_silently_broadening(indexed_mailbox, bounds):
    result = search(**bounds)
    assert result.startswith('Error: Search failed: date_'), result
    assert not uids(result)


def test_search_date_refinement_filters_imap_headers_before_limit(indexed_mailbox, monkeypatch):
    monkeypatch.setattr(es, 'SCHEDULED_EMAILS_DB', indexed_mailbox.with_name('missing.db'))
    dates = {b'10': 'Tue, 30 Jun 2026 23:59:59 +0000',
             b'20': 'Wed, 01 Jul 2026 09:00:00 +0900',
             b'30': 'Sat, 01 Aug 2026 09:00:00 +0900', b'40': 'unknown'}
    class Mailbox:
        def select(self, folder, readonly=False):
            assert readonly, 'search must not mark mail read'
            return 'OK', []
        def uid(self, operation, *args):
            if operation == 'SEARCH':
                assert 'SENTSINCE' in args[1] and 'SENTBEFORE' in args[1]
                return 'OK', [b'10 20 30 40']
            assert operation == 'FETCH'
            uid = args[0]
            header = f'Subject: Fixture receipt\r\nFrom: fixture@example.invalid\r\nDate: {dates[uid]}\r\n\r\n'
            return 'OK', [(b'header', header.encode())]
        def logout(self):
            pass
    monkeypatch.setattr(es, '_imap_connect', lambda account=None: Mailbox())
    result = search(date_from='2026-07-01T00:00:00Z', date_to='2026-08-01T00:00:00Z')
    assert uids(result) == ['20'], result
