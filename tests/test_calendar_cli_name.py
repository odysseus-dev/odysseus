from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_calendar_name_handles_missing_relation(monkeypatch):
    make_core_db_stub(monkeypatch, models=["CalendarCal", "CalendarEvent"])
    cli = load_script("odysseus-calendar")

    assert cli._calendar_name(SimpleNamespace(calendar=None)) == ""
    assert cli._calendar_name(SimpleNamespace(calendar=SimpleNamespace(name=123))) == ""
    assert cli._calendar_name(SimpleNamespace(calendar=SimpleNamespace(name="Work"))) == "Work"


def test_calendar_event_count_handles_bad_relationship(monkeypatch):
    make_core_db_stub(monkeypatch, models=["CalendarCal", "CalendarEvent"])
    cli = load_script("odysseus-calendar")

    assert cli._calendar_event_count(SimpleNamespace(events=[1, 2])) == 2
    assert cli._calendar_event_count(SimpleNamespace(events=None)) == 0
    assert cli._calendar_event_count(SimpleNamespace(events=object())) == 0
