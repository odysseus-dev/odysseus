import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


class _AuthManager:
    is_configured = True

    def __init__(self, admins=()):
        self._admins = set(admins)

    def is_admin(self, user):
        return user in self._admins


class _Request:
    def __init__(self, body, *, user="alice", admins=()):
        self._body = body
        self.state = SimpleNamespace(current_user=user)
        self.client = SimpleNamespace(host="127.0.0.1")
        self.app = SimpleNamespace(
            state=SimpleNamespace(auth_manager=_AuthManager(admins))
        )

    async def json(self):
        return self._body


class _Query:
    def __init__(self, note):
        self.note = note

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.note


class _Db:
    def __init__(self, note):
        self.note = note
        self.closed = False

    def query(self, model):
        return _Query(self.note)

    def close(self):
        self.closed = True


def _endpoint(monkeypatch, note=None):
    import routes.note_routes as note_routes

    calls = []
    db = _Db(note)

    async def fake_dispatch_reminder(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(note_routes, "SessionLocal", lambda: db)
    monkeypatch.setattr(note_routes, "dispatch_reminder", fake_dispatch_reminder)

    router = note_routes.setup_note_routes()
    endpoint = next(
        route.endpoint for route in router.routes
        if route.path == "/api/notes/fire-reminder" and "POST" in route.methods
    )
    return endpoint, calls, db


def _note(**overrides):
    data = {
        "id": "note-1",
        "owner": "alice",
        "title": "Stored title",
        "content": "Stored body",
        "items": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_real_reminder_requires_owned_note(monkeypatch):
    endpoint, calls, _db = _endpoint(monkeypatch, _note(owner="bob"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_Request({"note_id": "note-1"}, user="alice")))

    assert exc.value.status_code == 404
    assert calls == []


def test_real_reminder_uses_stored_note_and_ignores_overrides(monkeypatch):
    endpoint, calls, db = _endpoint(monkeypatch, _note())

    result = asyncio.run(endpoint(_Request({
        "note_id": "note-1",
        "title": "Forged title",
        "body": "Forged body",
        "channel": "webhook",
        "webhook_integration_id": "global-webhook",
        "webhook_payload_template": '{"content":"owned"}',
    }, user="alice")))

    assert result == {"ok": True}
    assert db.closed is True
    assert calls == [{
        "title": "Stored title",
        "note_body": "Stored body",
        "note_id": "note-1",
        "owner": "alice",
        "queue_browser": False,
        "settings_override": None,
    }]


def test_real_checklist_reminder_body_is_built_from_stored_items(monkeypatch):
    endpoint, calls, _db = _endpoint(monkeypatch, _note(items=(
        '[{"text":"first","done":false},'
        '{"text":"finished","done":true},'
        '{"text":"second","checked":false}]'
    )))

    asyncio.run(endpoint(_Request({"note_id": "note-1"}, user="alice")))

    assert calls[0]["note_body"] == "Pending (2):\n- first\n- second"


def test_non_admin_cannot_fire_synthetic_test_reminder(monkeypatch):
    endpoint, calls, _db = _endpoint(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_Request({
            "note_id": "test-123",
            "title": "Test Reminder",
            "body": "Test body",
            "channel": "webhook",
            "webhook_integration_id": "global-webhook",
        }, user="alice")))

    assert exc.value.status_code == 403
    assert calls == []


def test_admin_test_reminder_can_use_current_ui_overrides(monkeypatch):
    endpoint, calls, _db = _endpoint(monkeypatch)

    result = asyncio.run(endpoint(_Request({
        "note_id": "test-123",
        "title": "Test Reminder",
        "body": "Test body",
        "channel": "webhook",
        "webhook_integration_id": "global-webhook",
        "webhook_payload_template": '{"content":"{{message}}"}',
    }, user="admin", admins={"admin"})))

    assert result == {"ok": True}
    assert calls == [{
        "title": "Test Reminder",
        "note_body": "Test body",
        "note_id": "test-123",
        "owner": "admin",
        "queue_browser": False,
        "settings_override": {
            "reminder_channel": "webhook",
            "reminder_webhook_integration_id": "global-webhook",
            "reminder_webhook_payload_template": '{"content":"{{message}}"}',
        },
    }]
