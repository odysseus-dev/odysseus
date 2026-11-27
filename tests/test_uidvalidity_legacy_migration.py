"""Legacy-to-UIDVALIDITY migration tests; mocked IMAP only."""

import json
import pytest

from test_email_urgency_checkpoint import _configure_action


@pytest.mark.asyncio
async def test_legacy_upgrade_preserves_unrelated_account(monkeypatch, tmp_path):
    from routes import email_helpers
    import routes.note_routes as note_routes

    actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a", "acct-b"]
    )
    deliveries = []

    async def notify(**kwargs):
        deliveries.append(kwargs)
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    # First create real legacy-format state through the test scanner.
    await actions.action_check_email_urgency("alice")

    path = tmp_path / "email_urgency_state_alice.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    assert "acct-a:1" in before["per_uid"]
    assert "acct-b:1" in before["per_uid"]
    assert "acct-a:1" in before["notified_uids"]
    assert "acct-b:1" in before["notified_uids"]

    old_b = before["per_uid"]["acct-b:1"]

    connect = email_helpers._imap_connect

    class EpochImap:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def response(self, name):
            assert name == "UIDVALIDITY"
            return (name, [b"502"])

    monkeypatch.setattr(
        email_helpers,
        "_imap_connect",
        lambda *args, **kwargs: EpochImap(connect(*args, **kwargs)),
    )
    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")

    # The shared fake database ignores SQL filters. Simulate the
    # account-scoped query returning only the selected account.
    runtime["accounts"] = ["acct-a"]

    # Upgrade only account A; account B must not be changed.
    result, ok = await actions.action_check_email_urgency(
        "alice", prompt='{"account_id":"acct-a"}'
    )
    assert ok, result

    after = json.loads(path.read_text(encoding="utf-8"))
    assert "acct-a:502:1" in after["per_uid"]
    assert "acct-a:1" not in after["per_uid"]
    assert "acct-a:502:1" in after["notified_uids"]
    assert "acct-a:1" not in after["notified_uids"]
    print("BEFORE KEYS:", sorted(before["per_uid"]))
    print("AFTER KEYS:", sorted(after["per_uid"]))
    print("BEFORE NOTIFIED:", sorted(before["notified_uids"]))
    print("AFTER NOTIFIED:", sorted(after["notified_uids"]))
    assert after["per_uid"]["acct-b:1"] == old_b
    assert "acct-b:1" in after["notified_uids"]
    assert len(deliveries) == 2


@pytest.mark.asyncio
async def test_failed_upgrade_preserves_legacy_history(monkeypatch, tmp_path):
    import routes.note_routes as note_routes
    from src.builtin_actions import TaskNoop

    actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )

    async def notify(**kwargs):
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    await actions.action_check_email_urgency("alice")
    path = tmp_path / "email_urgency_state_alice.json"
    before = json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    runtime["failures"].add("acct-a")

    with pytest.raises(TaskNoop):
        await actions.action_check_email_urgency("alice")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["per_uid"] == before["per_uid"]
    assert after["notified_uids"] == before["notified_uids"]
