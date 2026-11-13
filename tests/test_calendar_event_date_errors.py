"""Malformed event dates are client errors and must not persist partial writes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.calendar_routes as calendar


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'calendar.db'}",
                           connect_args={"check_same_thread": False})
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(calendar, "SessionLocal", factory)
    app = FastAPI()

    @app.middleware("http")
    async def identify(request, call_next):
        request.state.current_user = "calendar-date-test"
        return await call_next(request)

    app.include_router(calendar.setup_calendar_routes())
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


INVALID_DATES = ["", " ", "not-a-date", "2030-02-30", "999999999999999999999"]


@pytest.mark.parametrize("field,value", [
    (field, value) for field in ["dtstart", "dtend"] for value in INVALID_DATES
    if not (field == "dtend" and value == "")
])
def test_invalid_create_is_400_and_rolls_back_default_calendar(api, field, value):
    client, factory = api
    payload = {"summary": "Must not persist", "dtstart": "2030-01-02T09:00:00",
               field: value}

    response = client.post("/api/calendar/events", json=payload)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": f"Invalid {field}"}
    with factory() as db:
        assert db.query(cdb.CalendarCal).count() == 0
        assert db.query(cdb.CalendarEvent).count() == 0


@pytest.mark.parametrize("field", ["dtstart", "dtend"])
@pytest.mark.parametrize("value", INVALID_DATES)
def test_invalid_update_is_400_and_keeps_the_original_event(api, field, value):
    client, factory = api
    created = client.post("/api/calendar/events", json={
        "summary": "Original", "dtstart": "2030-01-02T09:00:00+03:00",
        "dtend": "2030-01-02T10:00:00+03:00", "location": "Original location",
    })
    assert created.status_code == 200
    uid = created.json()["uid"]
    original = client.get(f"/api/calendar/events/{uid}").json()["event"]

    response = client.put(f"/api/calendar/events/{uid}", json={
        "summary": "Must not persist", "location": "Changed", field: value,
    })

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": f"Invalid {field}"}
    assert client.get(f"/api/calendar/events/{uid}").json()["event"] == original
    with factory() as db:
        assert db.query(cdb.CalendarEvent).count() == 1


@pytest.mark.parametrize("start,end,all_day,expected_start,expected_end", [
    ("2030-01-02T09:00:00+03:00", "2030-01-02T10:00:00+03:00", False,
     "2030-01-02T06:00:00Z", "2030-01-02T07:00:00Z"),
    ("2030-01-02T09:00:00Z", None, False,
     "2030-01-02T09:00:00Z", "2030-01-02T10:00:00Z"),
    ("2030-01-02", None, True, "2030-01-02", "2030-01-03"),
    ("2030-01-02T09:00:00", "", False,
     "2030-01-02T09:00:00", "2030-01-02T10:00:00"),
    ("January 2, 2030 9am", "January 2, 2030 10am", False,
     "2030-01-02T09:00:00", "2030-01-02T10:00:00"),
])
def test_valid_create_and_update_keep_supported_formats(api, start, end, all_day,
                                                       expected_start, expected_end):
    client, _ = api
    created = client.post("/api/calendar/events", json={
        "summary": "Supported", "dtstart": start, "dtend": end, "all_day": all_day,
    })
    assert created.status_code == 200, created.text
    uid = created.json()["uid"]
    saved = client.get(f"/api/calendar/events/{uid}").json()["event"]
    assert saved["dtstart"] == expected_start
    assert saved["dtend"] == expected_end
    updated = client.put(f"/api/calendar/events/{uid}", json={
        "summary": "Updated", "dtstart": start,
        "dtend": end if end else None, "all_day": all_day,
    })
    assert updated.status_code == 200, updated.text
    saved = client.get(f"/api/calendar/events/{uid}").json()["event"]
    assert saved["summary"] == "Updated"
    assert saved["dtstart"] == expected_start
    assert saved["dtend"] == expected_end
