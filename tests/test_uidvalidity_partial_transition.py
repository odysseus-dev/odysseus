"""Diagnostic: mailbox epoch transition during an incomplete scan."""
import json
import pytest

from test_email_urgency_checkpoint import _configure_action


@pytest.mark.asyncio
async def test_partial_epoch_transition_reports_state(monkeypatch, tmp_path):
    from routes import email_helpers
    import routes.note_routes as note_routes

    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    actions, runtime = _configure_action(monkeypatch, tmp_path, ["acct-a"])

    original_connect = email_helpers._imap_connect
    epoch = [501]
    partial = [False]

    class EpochImap:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def response(self, name):
            assert name == "UIDVALIDITY"
            return name, [str(epoch[0]).encode("ascii")]

        def uid(self, command, *args):
            if partial[0] and str(command).upper() == "FETCH":
                return "NO", [b"simulated partial FETCH failure"]
            return self.inner.uid(command, *args)

    monkeypatch.setattr(
        email_helpers,
        "_imap_connect",
        lambda *a, **kw: EpochImap(original_connect(*a, **kw)),
    )

    async def notify(**kwargs):
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    first, ok = await actions.action_check_email_urgency("alice")
    assert ok, first

    path = tmp_path / "email_urgency_state_alice.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    assert "acct-a:501:1" in before["per_uid"]

    epoch[0] = 502
    partial[0] = True

    try:
        await actions.action_check_email_urgency("alice")
    except actions.TaskNoop:
        pass

    after = json.loads(path.read_text(encoding="utf-8"))

    print("OLD GENERATION:", sorted(before["per_uid"]))
    print("AFTER PARTIAL SCAN:", sorted(after["per_uid"]))
    print("NOTIFICATION HISTORY:", sorted(after["notified_uids"]))
    print("GENERATIONS:", after.get("account_generations"))

    assert "acct-a:501:1" not in after["per_uid"]
    assert after["notified_uids"] == []
    assert after["account_generations"]["acct-a"]["mailbox_epoch"] == "502"


@pytest.mark.asyncio
async def test_mixed_partial_epoch_transition_reports_state(monkeypatch, tmp_path):
    from routes import email_helpers
    import routes.note_routes as note_routes

    monkeypatch.setenv("ODYAI_EMAIL_UIDVALIDITY_STRICT", "1")
    actions, runtime = _configure_action(monkeypatch, tmp_path, ["acct-a"])

    runtime["search_uids"]["acct-a"] = ("1", "2")

    base_connect = email_helpers._imap_connect
    epoch = [501]
    fail_uid_two = [False]

    class EpochImap:
        def __init__(self, inner):
            self.inner = inner

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def response(self, name):
            assert name == "UIDVALIDITY"
            return name, [str(epoch[0]).encode("ascii")]

        def uid(self, command, *args):
            if (
                fail_uid_two[0]
                and str(command).upper() == "FETCH"
                and args
                and str(args[0]).strip("b'") == "2"
            ):
                return "NO", [b"simulated UID 2 failure"]
            return self.inner.uid(command, *args)

    monkeypatch.setattr(
        email_helpers,
        "_imap_connect",
        lambda *a, **kw: EpochImap(base_connect(*a, **kw)),
    )

    async def notify(**kwargs):
        return {"browser_sent": True}

    monkeypatch.setattr(note_routes, "dispatch_reminder", notify)

    first, ok = await actions.action_check_email_urgency("alice")
    assert ok, first

    path = tmp_path / "email_urgency_state_alice.json"
    before = json.loads(path.read_text(encoding="utf-8"))

    assert "acct-a:501:1" in before["per_uid"]
    assert "acct-a:501:2" in before["per_uid"]

    epoch[0] = 502
    fail_uid_two[0] = True

    second, ok = await actions.action_check_email_urgency("alice")
    assert ok, second

    after = json.loads(path.read_text(encoding="utf-8"))

    print("BEFORE:", sorted(before["per_uid"]))
    print("AFTER:", sorted(after["per_uid"]))
    print("NOTIFIED:", sorted(after["notified_uids"]))
    print("GENERATIONS:", after.get("account_generations"))

    assert "acct-a:502:1" in after["per_uid"], (
        "Successfully fetched new-generation UID 1 was lost"
    )
    assert "acct-a:501:1" not in after["per_uid"]
    assert "acct-a:501:2" not in after["per_uid"]
    assert "acct-a:501:1" not in after["notified_uids"]
    assert "acct-a:501:2" not in after["notified_uids"]
    assert "acct-a:502:1" in after["notified_uids"]
    assert after["account_generations"]["acct-a"]["mailbox_epoch"] == "502"
    assert after["total_unread"] == 1
