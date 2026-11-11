"""The bounded arrival batch is displayed by Date, rather than arrival ID."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from tests.cli.test_mail_cli_read_empty_fetch import _load_mail_cli


@pytest.mark.parametrize('dates,expected', [
    (['Wed, 03 Jun 2026 12:00:00 +0000', 'Mon, 01 Jun 2026 09:00:00 +0000'], ['1', '2']),
    (['Wed, 03 Jun 2026 12:00:00 +0000', 'Wed, 03 Jun 2026 13:00:00 +0200'], ['1', '2']),
    (['Wed, 03 Jun 2026 12:00:00', 'Wed, 03 Jun 2026 11:00:00 +0000'], ['1', '2']),
    (['Wed, 03 Jun 2026 12:00:00 +0000', None, 'not a date'], ['1', '3', '2']),
    (['Wed, 03 Jun 2026 12:00:00 +0000', 'Wed, 03 Jun 2026 14:00:00 +0200'], ['2', '1']),
    (['Wed, 03 Jun 2026 12:00:00 +0000', 'Thu, 04 Jun 2026 12:00:00 +0000'], ['2', '1']),
    (['Wed, 31 Dec 1969 12:00:00 +0000', None], ['1', '2']),
])
def test_list_sorts_parsed_dates_without_changing_row_schema(monkeypatch, dates, expected):
    cli = _load_mail_cli(monkeypatch)
    fetched = []

    class Connection:
        def select(self, folder, readonly=True):
            assert readonly is True
            return 'OK', [str(len(dates)).encode()]

        def search(self, *_):
            return 'OK', [' '.join(str(i+1) for i in range(len(dates))).encode()]

        def fetch(self, uid, _):
            fetched.append(uid.decode())
            date = dates[int(uid)-1]
            raw = 'From: Sender <sender@example.com>\r\nSubject: Item '+uid.decode()+'\r\n'
            if date is not None:
                raw += 'Date: '+date+'\r\n'
            return 'OK', [(b'1 (FLAGS (\\Seen) RFC822.HEADER)', (raw+'\r\n').encode())]

    @contextmanager
    def imap(_):
        yield Connection()

    rows = []
    monkeypatch.setattr(cli, '_imap', imap)
    monkeypatch.setattr(cli, 'emit', lambda value, args: rows.extend(value))
    cli.cmd_list(SimpleNamespace(account='fixture', folder='INBOX', limit=20))
    assert [row['uid'] for row in rows] == expected
    assert fetched == [str(i) for i in range(len(dates), 0, -1)]
    assert all(set(row) == {'uid', 'date', 'from_addr', 'from_name', 'subject', 'is_read', 'is_answered'} for row in rows)
    assert all(row['is_read'] and not row['is_answered'] for row in rows)


def test_list_still_limits_the_arrival_batch(monkeypatch):
    cli = _load_mail_cli(monkeypatch)
    fetched = []
    class Connection:
        def select(self, *_args, **_kwargs):return 'OK', []
        def search(self, *_):return 'OK', [b'1 2 3']
        def fetch(self, uid, _):
            fetched.append(uid)
            return 'OK', [(b'3 (FLAGS () RFC822.HEADER)', b'From: x@example.com\r\nSubject: bounded\r\n\r\n')]
    @contextmanager
    def imap(_):yield Connection()
    monkeypatch.setattr(cli, '_imap', imap)
    monkeypatch.setattr(cli, 'emit', lambda *_:None)
    cli.cmd_list(SimpleNamespace(account='fixture', folder='INBOX', limit=1))
    assert fetched == [b'3']
