"""Background note reminders retry failed primary delivery without losing receipts."""
import datetime
import json
from types import SimpleNamespace

import httpx
import pytest

from tests.helpers.database import disposable_database


@pytest.fixture
def reminder_scan(tmp_path, monkeypatch):
    from core import database
    from routes import note_routes
    from src import builtin_actions, integrations, settings

    instant = datetime.datetime(2026, 10, 7, 12, tzinfo=datetime.timezone.utc)

    class Clock(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    calls, notices = [], []
    statuses = [503, 200]
    channel = {"reminder_channel": "webhook", "reminder_webhook_integration_id": "audit", "reminder_webhook_payload_template": '{"message":"{{message}}"}'}

    def deliver(request):
        calls.append(request)
        return httpx.Response(statuses.pop(0))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(deliver), **kw))
    monkeypatch.setattr(settings, "load_settings", lambda: channel)
    monkeypatch.setattr(integrations, "load_integrations", lambda: [{"id": "audit", "preset": "ntfy", "base_url": "https://reminder.example.test", "enabled": True}])
    monkeypatch.setattr(note_routes, "_scheduler_ref", SimpleNamespace(add_notification=lambda **kw: notices.append(kw)))
    monkeypatch.setattr(note_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(datetime, "datetime", Clock)
    monkeypatch.setattr("src.url_safety.check_outbound_url", lambda *a, **kw: (True, ""))
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(database, "SessionLocal", factory)
        with factory() as db:
            db.add(database.Note(id="note-1", owner="alice", title="Due note", due_date=instant.isoformat()))
            db.commit()
        yield SimpleNamespace(
            scan=lambda: builtin_actions.action_ping_notes("alice"),
            path=tmp_path / "note_pings_alice.json", calls=calls, notices=notices,
            statuses=statuses, channel=channel, instant=instant, factory=factory,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["webhook", "ntfy"])
async def test_failed_primary_is_retried_and_receipt_keeps_channel(reminder_scan, channel):
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    scan.channel["reminder_channel"] = channel
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert len(scan.calls) == 1
    assert json.loads(scan.path.read_text())["note-1"]["channel"] == "browser"
    message, ok = await scan.scan()
    assert ok is True and "Pinged 1" in message
    assert len(scan.calls) == 2
    assert len(scan.notices) == 1
    assert json.loads(scan.path.read_text())["note-1"]["channel"] == channel
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert len(scan.calls) == 2


@pytest.mark.asyncio
async def test_legacy_browser_receipt_allows_external_retry(reminder_scan):
    scan = reminder_scan
    scan.path.write_text(json.dumps({"note-1": scan.instant.isoformat()}))
    scan.statuses[:] = [200]
    message, ok = await scan.scan()
    assert ok is True and "Pinged 1" in message
    assert len(scan.calls) == 1 and scan.notices == []
    assert json.loads(scan.path.read_text())["note-1"]["channel"] == "webhook"


@pytest.mark.asyncio
async def test_browser_delivery_is_deduped_and_typed(reminder_scan):
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    scan.channel["reminder_channel"] = "browser"
    _, ok = await scan.scan()
    assert ok is True
    assert json.loads(scan.path.read_text())["note-1"]["channel"] == "browser"
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert len(scan.notices) == 1 and scan.calls == []


@pytest.mark.asyncio
async def test_total_delivery_failure_does_not_checkpoint(reminder_scan, monkeypatch):
    from routes import note_routes
    from src.builtin_actions import TaskNoop
    scan = reminder_scan
    monkeypatch.setattr(note_routes, "_scheduler_ref", None)
    with pytest.raises(TaskNoop):
        await scan.scan()
    assert "note-1" not in json.loads(scan.path.read_text())
    _, ok = await scan.scan()
    assert ok is True and len(scan.calls) == 2


@pytest.mark.asyncio
async def test_pruning_retains_dispatcher_receipt_and_other_due_note(reminder_scan):
    from core import database
    scan = reminder_scan
    with scan.factory() as db:
        db.add(database.Note(id="later", owner="alice", title="Later", due_date=(scan.instant + datetime.timedelta(days=1)).isoformat()))
        db.commit()
    scan.path.write_text(json.dumps({"gone": "old", "later": {"at": scan.instant.isoformat(), "channel": "email"}}))
    scan.statuses[:] = [200]
    _, ok = await scan.scan()
    assert ok is True
    cache = json.loads(scan.path.read_text())
    assert set(cache) == {"note-1", "later"}
    assert cache["note-1"]["channel"] == "webhook"
    assert cache["later"]["channel"] == "email"
