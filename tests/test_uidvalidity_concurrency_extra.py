"""Staging-only regression: an old mailbox epoch cannot overwrite a newer epoch."""
import asyncio
import json
import pytest
from test_email_urgency_checkpoint import _configure_action


@pytest.mark.asyncio
async def test_stale_mailbox_epoch_cannot_overwrite_newer_notification(
    monkeypatch, tmp_path
):
    from routes import email_helpers
    import routes.note_routes as note_routes

    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    actions, runtime = _configure_action(monkeypatch, tmp_path, ["acct-a"])
    base_connect = email_helpers._imap_connect
    epoch = [501]

    class EpochImap:
        def __init__(self, conn, snapshot):
            self.conn = conn
            self.snapshot = snapshot

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def response(self, name):
            assert name == "UIDVALIDITY"
            return name, [str(self.snapshot).encode("ascii")]

    def connect(*args, **kwargs):
        return EpochImap(base_connect(*args, **kwargs), epoch[0])

    monkeypatch.setattr(email_helpers, "_imap_connect", connect)
    deliveries = []

    async def notify(**kwargs):
        deliveries.append(kwargs["note_body"])
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    original = actions._run_email_urgency_state_transaction
    old_waiting = asyncio.Event()
    allow_old = asyncio.Event()
    dispatch_index = 0

    async def ordered(state_path, lock_path, operation):
        nonlocal dispatch_index
        if operation.__name__ == "_dispatch_and_checkpoint":
            dispatch_index += 1
            if dispatch_index == 1:
                old_waiting.set()
                await allow_old.wait()
        return await original(state_path, lock_path, operation)

    monkeypatch.setattr(actions, "_run_email_urgency_state_transaction", ordered)

    older_task = asyncio.create_task(actions.action_check_email_urgency("alice"))
    try:
        await asyncio.wait_for(old_waiting.wait(), timeout=5)
        epoch[0] = 502
        newer = await asyncio.wait_for(
            actions.action_check_email_urgency("alice"), timeout=5
        )
    finally:
        allow_old.set()

    older = await asyncio.wait_for(older_task, timeout=5)
    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )

    assert newer[1] is True
    assert older[1] is True
    assert set(state["per_uid"]) == {"acct-a:502:1"}
    assert state["notified_uids"] == ["acct-a:502:1"]
    assert len(deliveries) == 1, "Stale mailbox epoch must not trigger a notification"
    assert "acct-a" in deliveries[0]
