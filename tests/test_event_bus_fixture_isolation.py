import asyncio

import src.event_bus as event_bus


def test_fixture_owners_do_not_run_event_automations():
    assert not event_bus._event_automation_enabled_for_owner("sft_alex_creator")
    assert not event_bus._event_automation_enabled_for_owner("SFT_MAYA_OPS")
    assert event_bus._event_automation_enabled_for_owner("pewds")
    assert event_bus._event_automation_enabled_for_owner(None)


def test_fixture_event_returns_before_database_access(monkeypatch):
    import core.database

    def fail_if_opened():
        raise AssertionError("fixture event must not open a database session")

    monkeypatch.setattr(core.database, "SessionLocal", fail_if_opened)
    asyncio.run(event_bus._handle_event("session_created", "sft_alex_creator"))
