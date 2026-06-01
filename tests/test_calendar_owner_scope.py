"""Pin owner-scoping of the autonomous email->calendar event snapshot.

The email auto-calendar pass fans out over EVERY user's mailbox and used to
feed an *unscoped* upcoming-events snapshot to the extraction LLM, then execute
the model's create/update/delete ops via do_manage_calendar with owner=None —
so processing one tenant's mail could read AND mutate another tenant's calendar
(and leak every tenant's event titles to the LLM endpoint).

The fix routes the snapshot through core.database.get_upcoming_events(owner)
and passes the account owner to do_manage_calendar. This test pins that
get_upcoming_events scopes to the owner; it fails if the owner filter is
dropped (the original cross-tenant behavior).
"""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from datetime import datetime, timedelta

from core import database as db


def test_get_upcoming_events_is_owner_scoped():
    db.Base.metadata.create_all(bind=db.engine)
    soon = datetime.utcnow() + timedelta(days=2)
    end = soon + timedelta(hours=1)

    s = db.SessionLocal()
    try:
        s.merge(db.CalendarCal(id="cal-alice", owner="alice", name="Alice"))
        s.merge(db.CalendarCal(id="cal-bob", owner="bob", name="Bob"))
        s.merge(db.CalendarEvent(uid="ev-alice", calendar_id="cal-alice",
                                 summary="Alice 1:1", dtstart=soon, dtend=end))
        s.merge(db.CalendarEvent(uid="ev-bob", calendar_id="cal-bob",
                                 summary="Bob 1:1", dtstart=soon, dtend=end))
        s.commit()
    finally:
        s.close()

    alice = {e["uid"] for e in db.get_upcoming_events("alice")}
    bob = {e["uid"] for e in db.get_upcoming_events("bob")}
    everyone = {e["uid"] for e in db.get_upcoming_events(None)}

    # An owner sees ONLY their own events — never the other tenant's.
    assert alice == {"ev-alice"}, alice
    assert bob == {"ev-bob"}, bob
    assert "ev-bob" not in alice and "ev-alice" not in bob
    # owner=None is the explicit single-user / legacy escape hatch (unscoped).
    assert {"ev-alice", "ev-bob"} <= everyone
