"""Daily brief must query and display each calendar storage kind in local time."""
import asyncio
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core import database
from src import builtin_actions


@contextmanager
def system_timezone(name):
    previous = os.environ.get('TZ')
    os.environ['TZ'] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()


@pytest.mark.skipif(not hasattr(time, 'tzset'), reason='requires changing the process local timezone')
@pytest.mark.parametrize('zone', ['Europe/Berlin', 'America/Los_Angeles'])
def test_daily_brief_selects_local_day_and_orders_mixed_storage(monkeypatch, zone):
    engine = create_engine('sqlite://', poolclass=StaticPool)
    for model in [database.CalendarCal, database.CalendarEvent, database.Note]:
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, 'SessionLocal', sessions)
    from core.auth import AuthManager
    monkeypatch.setattr(AuthManager, 'is_configured', property(lambda self: True))
    from routes import email_helpers
    def no_email(*args):
        raise RuntimeError('No email account in this calendar-only test')
    monkeypatch.setattr(email_helpers, '_imap_connect', no_email)
    try:
        with system_timezone(zone):
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            def utc(value):
                return value.astimezone(timezone.utc).replace(tzinfo=None)
            with sessions() as db:
                for owner in ['alice', 'bob', None]:
                    db.add(database.CalendarCal(id=owner or 'shared', owner=owner, name=owner or 'shared'))
                db.flush()
                def add(uid, start, end, *, utc_row=False, all_day=False, owner='alice', status='confirmed'):
                    db.add(database.CalendarEvent(uid=uid, calendar_id=owner or 'shared', summary=uid,
                        dtstart=utc(start) if utc_row and not all_day else start,
                        dtend=utc(end) if utc_row and not all_day else end,
                        is_utc=utc_row, all_day=all_day, status=status))
                add('early', today+timedelta(minutes=30), today+timedelta(hours=1), utc_row=True)
                add('utc-ten', today+timedelta(hours=10), today+timedelta(hours=11), utc_row=True)
                add('local-nine', today+timedelta(hours=9), today+timedelta(hours=10))
                add('all-day', today, today+timedelta(days=1), utc_row=True, all_day=True)
                add('next-day', today+timedelta(days=1,minutes=30), today+timedelta(days=1,hours=1), utc_row=True)
                add('ended', today-timedelta(hours=1), today, utc_row=True)
                add('cancelled', today+timedelta(hours=12), today+timedelta(hours=13), utc_row=True, status='cancelled')
                add('foreign', today+timedelta(hours=12), today+timedelta(hours=13), utc_row=True, owner='bob')
                add('unowned', today+timedelta(hours=12), today+timedelta(hours=13), owner=None)
                db.commit()
            text, success = asyncio.run(builtin_actions.action_daily_brief('alice'))
            assert success, text
            lines = ['all day  all-day', '00:30  early', '09:00  local-nine', '10:00  utc-ten']
            assert all(line in text for line in lines), text
            assert [text.index(line) for line in lines] == sorted(text.index(line) for line in lines), text
            for excluded in ['next-day', 'ended', 'cancelled', 'foreign', 'unowned']:
                assert excluded not in text, text
    finally:
        engine.dispose()


@pytest.mark.parametrize('day, hours', [('2026-03-29', 23), ('2026-10-25', 25)])
def test_utc_day_window_uses_offset_at_each_boundary(day, hours):
    start = datetime.fromisoformat(day)
    zone = ZoneInfo('Europe/Berlin')
    begin = builtin_actions._daily_brief_utc_boundary(start, zone)
    end = builtin_actions._daily_brief_utc_boundary(start+timedelta(days=1), zone)
    assert end-begin == timedelta(hours=hours)
    if hasattr(time, 'tzset'):
        with system_timezone('Europe/Berlin'):
            assert builtin_actions._daily_brief_utc_boundary(start) == begin
            assert builtin_actions._daily_brief_utc_boundary(start+timedelta(days=1)) == end


@pytest.mark.parametrize('utc_row, all_day, expected', [
    (True, False, datetime(2026, 6, 1, 10)),
    (False, False, datetime(2026, 6, 1, 8)),
    (True, True, datetime(2026, 6, 1, 8)),
])
def test_local_start_preserves_floating_and_date_only_values(utc_row, all_day, expected):
    event = SimpleNamespace(dtstart=datetime(2026,6,1,8), is_utc=utc_row, all_day=all_day)
    assert builtin_actions._daily_brief_local_start(event, ZoneInfo('Europe/Berlin')) == expected
