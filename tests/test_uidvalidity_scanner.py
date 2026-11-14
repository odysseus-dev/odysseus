"""Experimental UIDVALIDITY integration tests; all network and dispatch mocked."""
import json
import pytest
from test_email_urgency_checkpoint import _configure_action


@pytest.mark.asyncio
async def test_mailbox_epoch_transition_isolates_notifications(monkeypatch, tmp_path):
    from routes import email_helpers
    import routes.note_routes as note_routes

    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    actions, runtime = _configure_action(monkeypatch, tmp_path, ["acct-a"])
    imap_connect = email_helpers._imap_connect
    epoch = [501]

    class EpochImap:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def response(self, name):
            assert name == "UIDVALIDITY"
            return (name, [str(epoch[0]).encode("ascii")])

    monkeypatch.setattr(email_helpers, "_imap_connect", lambda *a, **kw: EpochImap(imap_connect(*a, **kw)))
    calls = []

    async def notify(**kw):
        calls.append(kw)
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    first, ok = await actions.action_check_email_urgency("alice")
    assert ok, first
    state_path = tmp_path / "email_urgency_state_alice.json"
    state = json.loads(state_path.read_text())
    assert "acct-a:501:1" in state["per_uid"]
    assert "acct-a:501:1" in state["notified_uids"]
    assert len(calls) == 1

    epoch[0] = 502
    second, ok = await actions.action_check_email_urgency("alice")
    assert ok, second
    state = json.loads(state_path.read_text())
    assert "acct-a:502:1" in state["per_uid"]
    assert "acct-a:501:1" not in state["per_uid"]
    assert "acct-a:502:1" in state["notified_uids"]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_unavailable_uidvalidity_does_not_trust_cache(monkeypatch, tmp_path):
    from routes import email_helpers
    from src.builtin_actions import TaskNoop
    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    actions, runtime = _configure_action(monkeypatch, tmp_path, ["acct-a"])
    # Fake IMAP deliberately has no response method. Scan fails closed.
    with pytest.raises(TaskNoop):
        await actions.action_check_email_urgency("alice")
    cache_path = tmp_path / "urgency-cache" / "acct-a.json"
    assert not cache_path.exists()
