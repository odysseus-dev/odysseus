import sqlite3

import pytest


@pytest.mark.asyncio
async def test_builtin_email_actions_pass_task_owner_to_poller(monkeypatch):
    import routes.email_pollers as email_pollers
    from src.builtin_actions import (
        action_draft_email_replies,
        action_extract_email_events,
        action_summarize_emails,
    )

    calls = []

    async def fake_run_auto_summarize_once(**kwargs):
        calls.append(kwargs)
        return "Processed 1 emails"

    monkeypatch.setattr(email_pollers, "_run_auto_summarize_once", fake_run_auto_summarize_once)

    summary_msg, summary_ok = await action_summarize_emails("alice")
    draft_msg, draft_ok = await action_draft_email_replies("alice")
    events_msg, events_ok = await action_extract_email_events("alice")

    assert summary_ok is True
    assert draft_ok is True
    assert events_ok is True
    assert summary_msg == "Processed 1 emails"
    assert draft_msg == "Processed 1 emails"
    assert events_msg == "Processed 1 emails (3d window)"
    assert [call.get("owner") for call in calls] == ["alice", "alice", "alice"]


@pytest.mark.asyncio
async def test_legacy_email_summary_uses_task_owner_for_model_resolution(monkeypatch, tmp_path):
    import routes.email_pollers as email_pollers
    import src.endpoint_resolver as endpoint_resolver

    scheduled_db = tmp_path / "scheduled.db"
    with sqlite3.connect(scheduled_db) as conn:
        conn.execute("CREATE TABLE email_summaries (message_id TEXT, owner TEXT)")
        conn.execute("CREATE TABLE email_ai_replies (message_id TEXT, owner TEXT)")

    captured = {}

    class _Conn:
        def select(self, folder, readonly=True):
            captured.setdefault("selects", []).append((folder, readonly))
            return "OK", []

        def uid(self, command, *args):
            captured.setdefault("uid_calls", []).append((command, args))
            if command == "SEARCH":
                return "OK", [b"123"]
            raise AssertionError(f"unexpected uid command: {command}")

        def logout(self):
            captured["logout"] = captured.get("logout", 0) + 1

    def fake_imap_connect(account_id=None, owner=""):
        captured["imap_connect"] = {"account_id": account_id, "owner": owner}
        return _Conn()

    def fake_get_email_config(account_id=None, owner=""):
        captured["email_config"] = {"account_id": account_id, "owner": owner}
        return {"from_address": "alice@example.test"}

    def fake_resolve_endpoint(setting_prefix, *args, owner=None, **kwargs):
        captured.setdefault("resolve_calls", []).append((setting_prefix, owner))
        return None, None, {}

    monkeypatch.setattr(email_pollers, "SCHEDULED_DB", str(scheduled_db))
    monkeypatch.setattr(email_pollers, "_load_settings", lambda: {"email_auto_summarize": True})
    monkeypatch.setattr(email_pollers, "_owner_for_email_account", lambda account_id: "")
    monkeypatch.setattr(email_pollers, "_imap_connect", fake_imap_connect)
    monkeypatch.setattr(email_pollers, "_get_email_config", fake_get_email_config)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)

    result = await email_pollers._auto_summarize_pass_single(owner="alice")

    assert result == "No model configured"
    assert captured["imap_connect"] == {"account_id": None, "owner": "alice"}
    assert captured["email_config"] == {"account_id": None, "owner": "alice"}
    assert captured["resolve_calls"] == [("utility", "alice"), ("default", "alice")]
    assert captured["logout"] == 1
