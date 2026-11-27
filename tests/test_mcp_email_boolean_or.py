"""OR searches must keep the index's account/owner/folder/date boundaries."""
import sqlite3
from datetime import datetime, timezone

import pytest

import mcp_servers.email_server as es


@pytest.fixture
def indexed_mail(tmp_path, monkeypatch):
    path = tmp_path / 'mail.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE email_message_index (
            owner TEXT, account_key TEXT, folder TEXT, uid TEXT,
            message_id TEXT, subject TEXT, from_name TEXT, from_address TEXT,
            to_text TEXT, cc_text TEXT, attachment_names TEXT,
            date_iso TEXT, date_display TEXT, date_epoch REAL)''')
        rows = [
            ('alice', 'work', 'INBOX', '1', 'unsubscribe'),
            ('alice', 'work', 'INBOX', '2', 'sale'),
            ('alice', 'work', 'INBOX', '3', 'deal'),
            ('alice', 'work', 'INBOX', '4', 'rock OR roll'),
            ('alice', 'work', 'INBOX', '5', 'sale deal bundle'),
            ('alice', 'work', 'INBOX', '6', 'save_%literal'),
            ('bob', 'work', 'INBOX', '99', 'sale'),
            ('alice', 'personal', 'INBOX', '88', 'sale'),
            ('alice', 'work', 'Spam', '77', 'sale'),
        ]
        for owner, account, folder, uid, subject in rows:
            date = datetime(2026, 11, 11, 8, int(uid), tzinfo=timezone.utc) if int(uid) < 60 else datetime(2026, 11, 11, tzinfo=timezone.utc)
            db.execute('INSERT INTO email_message_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (owner, account, folder, uid, '<' + uid + '>', subject, 'Sender',
                        'sender@example.invalid', 'alice@example.invalid', '', '',
                        date.isoformat(), date.isoformat(), date.timestamp()))
    monkeypatch.setattr(es, 'SCHEDULED_EMAILS_DB', str(path))
    monkeypatch.setattr(es, '_list_accounts_raw', lambda: [{'id': 'work', 'name': 'Work'}])
    monkeypatch.setattr(es, '_load_config', lambda account=None: {'account_id': account or 'work'})
    monkeypatch.setattr(es, '_get_cached_summaries', lambda: {})
    token = es._CURRENT_OWNER.set('alice')
    yield
    es._CURRENT_OWNER.reset(token)


@pytest.mark.parametrize(('query', 'expected'), [
    ('unsubscribe OR sale OR deal', {'1', '2', '3', '5'}),
    ('sale or deal', {'2', '3', '5'}),
    ('sale Or deal', {'2', '3', '5'}),
    ('"rock OR roll" OR deal', {'3', '4', '5'}),
    ('sale deal OR unsubscribe', {'1', '5'}),
    ('"OR" OR unsubscribe', {'1', '4'}),
    ('save_%literal OR unsubscribe', {'1', '6'}),
    ('sale OR missing', {'2', '5'}),
    ('sale OR sale', {'2', '5'}),
    ('s OR missing', {'1', '2', '3', '4', '5', '6'}),
    ('sale', {'2', '5'}),
    ('sale deal', {'5'}),
    ('"rock OR roll"', {'4'}),
    ('OR', {'4'}),
    ('unmatched "quote', set()),
    ('sale OR', set()),
    ('OR sale', set()),
    ('sale OR OR deal', set()),
])
def test_indexed_boolean_or_matches_expected_owned_messages(indexed_mail, query, expected):
    hits = es._indexed_search_emails(query, folders=['INBOX'], account='work', max_results=100)
    assert hits is not None
    assert {hit['uid'] for hit in hits} == expected


def test_boolean_or_keeps_date_and_result_limit(indexed_mail):
    hits = es._indexed_search_emails('sale OR deal', folders=['INBOX'], account='work',
                                   date_from='2026-11-11', date_to='2026-11-12', max_results=2)
    assert [hit['uid'] for hit in hits] == ['5', '3']
    assert es._indexed_search_emails('sale OR deal', folders=['INBOX'], account='work',
                                    date_from='2026-11-12', max_results=100) == []


def test_or_branches_share_the_existing_six_term_budget(indexed_mail):
    query = ' OR '.join(['absent1', 'absent2', 'absent3', 'absent4', 'absent5', 'absent6', 'sale'])
    assert es._indexed_search_emails(query, folders=['INBOX'], account='work', max_results=100) == []
