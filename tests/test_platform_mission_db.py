# tests/test_platform_mission_db.py
"""Mission/MissionTask ORM round-trip (platform mission loop, Plan 3)."""
from core.database import SessionLocal, Mission, MissionTask


def test_mission_and_tasks_roundtrip():
    db = SessionLocal()
    try:
        m = Mission(id="mis-1", goal="grow organic traffic", owner="oleg",
                    status="planning")
        db.add(m)
        db.add(MissionTask(
            id="mt-1", mission_id="mis-1", seq=0,
            target_company="general-office-1", intent="quote.create",
            task_text="prepare an SEO retainer quote",
            conversation_id="mission:mis-1:task:mt-1", status="pending"))
        db.commit()
        got = db.query(Mission).filter_by(id="mis-1").one()
        assert got.status == "planning" and got.report is None
        tasks = db.query(MissionTask).filter_by(mission_id="mis-1").all()
        assert len(tasks) == 1 and tasks[0].intent == "quote.create"
        assert tasks[0].conversation_id == "mission:mis-1:task:mt-1"
    finally:
        db.rollback()
        db.close()
