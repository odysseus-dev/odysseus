import asyncio
import json
import threading
from types import SimpleNamespace

import pytest


class _Column:
    def __eq__(self, _other):
        return True

    def __ne__(self, _other):
        return True


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return list(self._rows)


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def query(self, _model):
        return _Query(self._rows())

    def close(self):
        return None


class _EmailAccount:
    enabled = _Column()
    owner = _Column()
    imap_user = _Column()
    from_address = _Column()
    id = _Column()


class _FakeImap:
    def __init__(self, account_id, failures, seen_accounts):
        self.account_id = account_id
        self.failures = failures
        self.seen_accounts = seen_accounts

    def select(self, *_args, **_kwargs):
        if self.account_id in self.failures:
            raise RuntimeError(f"{self.account_id} unavailable")
        return "OK", []

    def uid(self, command, *_args):
        if self.account_id in self.failures:
            raise RuntimeError(f"{self.account_id} unavailable")
        if command == "SEARCH":
            return "OK", [b"1"]
        query = str(_args[-1]) if _args else ""
        seen = "\\Seen" if self.account_id in self.seen_accounts else ""
        flags = f"1 (UID 1 FLAGS ({seen}))".encode()
        if query == "(UID FLAGS)":
            return "OK", [flags]
        raw = (
            f"From: Sender {self.account_id} <sender-{self.account_id}@example.com>\r\n"
            f"Subject: Urgent request for {self.account_id}\r\n"
            f"Message-ID: <{self.account_id}-1@example.com>\r\n"
            "\r\n"
            "Please reply immediately."
        ).encode()
        return "OK", [(flags, raw)]

    def logout(self):
        return None


def _account(account_id):
    return SimpleNamespace(
        id=account_id,
        enabled=True,
        owner="alice",
        imap_user="alice",
        from_address="alice",
    )


def _configure_action(monkeypatch, tmp_path, account_ids):
    from core import database
    from routes import email_helpers
    from src import builtin_actions, llm_core, settings, task_endpoint

    runtime = {
        "accounts": list(account_ids),
        "failures": set(),
        "seen_accounts": set(),
        "settings": {
            "reminder_channel": "browser",
            "reminder_llm_synthesis": False,
            "app_public_url": "",
        },
    }

    monkeypatch.setattr(builtin_actions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        builtin_actions,
        "EMAIL_URGENCY_CACHE_DIR",
        str(tmp_path / "urgency-cache"),
    )
    monkeypatch.setattr(database, "EmailAccount", _EmailAccount)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: _Db(lambda: [_account(value) for value in runtime["accounts"]]),
    )
    monkeypatch.setattr(
        task_endpoint,
        "resolve_task_candidates",
        lambda *args, **kwargs: [("http://llm", "model", {})],
    )

    async def fake_fallback(*_args, **_kwargs):
        return '{"score": 3, "reason": "urgent"}'

    monkeypatch.setattr(llm_core, "llm_call_async_with_fallback", fake_fallback)
    monkeypatch.setattr(settings, "load_settings", lambda: dict(runtime["settings"]))
    monkeypatch.setattr(
        email_helpers,
        "SCHEDULED_DB",
        tmp_path / "scheduled-emails.db",
    )
    monkeypatch.setattr(
        email_helpers,
        "_imap_connect",
        lambda account_id=None, **_kwargs: _FakeImap(
            str(account_id), runtime["failures"], runtime["seen_accounts"]
        ),
    )
    return builtin_actions, runtime


@pytest.mark.asyncio
async def test_urgency_state_transaction_serializes_decision_and_checkpoint(tmp_path):
    """A later worker must observe the first worker's delivered UID."""
    from src.builtin_actions import _run_email_urgency_state_transaction

    state_path = tmp_path / "email_urgency_state_alice.json"
    lock_db = tmp_path / "urgency.lock.sqlite3"
    first_entered = asyncio.Event()
    allow_first_to_finish = asyncio.Event()
    second_entered = asyncio.Event()
    deliveries = []

    async def operation(name):
        async def update(prior):
            if name == "first":
                first_entered.set()
                await allow_first_to_finish.wait()
            else:
                second_entered.set()

            notified = set(prior.get("notified_uids", []))
            delivered = "acct:42" not in notified
            if delivered:
                deliveries.append(name)
                notified.add("acct:42")
            return delivered, {
                "owner": "alice",
                "notified_uids": sorted(notified),
            }

        return await _run_email_urgency_state_transaction(
            state_path,
            lock_db,
            update,
        )

    first = asyncio.create_task(operation("first"))
    await asyncio.wait_for(first_entered.wait(), timeout=2)
    second = asyncio.create_task(operation("second"))
    await asyncio.sleep(0.1)
    assert not second_entered.is_set()
    allow_first_to_finish.set()

    assert await asyncio.wait_for(first, timeout=2) is True
    assert await asyncio.wait_for(second, timeout=2) is False
    assert deliveries == ["first"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "owner": "alice",
        "notified_uids": ["acct:42"],
    }


@pytest.mark.asyncio
async def test_waiting_transaction_cancellation_does_not_leak_lock(tmp_path):
    from src.builtin_actions import _run_email_urgency_state_transaction

    state_path = tmp_path / "email_urgency_state_alice.json"
    lock_db = tmp_path / "urgency.lock.sqlite3"
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def first_operation(_prior):
        first_entered.set()
        await release_first.wait()
        return None, {"notified_uids": ["acct:1"]}

    async def later_operation(prior):
        return None, prior

    first = asyncio.create_task(
        _run_email_urgency_state_transaction(
            state_path, lock_db, first_operation
        )
    )
    await asyncio.wait_for(first_entered.wait(), timeout=2)
    waiting = asyncio.create_task(
        _run_email_urgency_state_transaction(
            state_path, lock_db, later_operation
        )
    )
    await asyncio.sleep(0.05)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, timeout=2)

    release_first.set()
    await asyncio.wait_for(first, timeout=2)
    await asyncio.wait_for(
        _run_email_urgency_state_transaction(
            state_path, lock_db, later_operation
        ),
        timeout=2,
    )


@pytest.mark.asyncio
async def test_action_dispatch_stays_on_app_loop_and_queues_browser_notification(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes
    from src import endpoint_resolver, llm_core
    from src.task_scheduler import TaskScheduler

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )
    runtime["settings"]["reminder_llm_synthesis"] = True
    monkeypatch.setattr(note_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda *_args, **_kwargs: (
            "https://api.openai.com/v1",
            "utility-model",
            {},
        ),
    )

    expected_loop = asyncio.get_running_loop()
    expected_thread = threading.get_ident()
    synthesis_loops = []
    shared_client = SimpleNamespace(is_closed=False)
    monkeypatch.setattr(llm_core, "_http_client", shared_client)
    monkeypatch.setattr(llm_core, "_response_cache", {})

    class _Response:
        is_success = True
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {
                "choices": [
                    {"message": {"content": "Synthesized urgency reminder."}}
                ]
            }

    async def fake_http_post(client, *_args, **_kwargs):
        synthesis_loops.append(asyncio.get_running_loop())
        assert client is shared_client
        return _Response()

    monkeypatch.setattr(
        llm_core,
        "httpx_post_kimi_aware_async",
        fake_http_post,
    )

    scheduler = TaskScheduler(None)
    notification_threads = []
    original_add = scheduler.add_notification

    def checked_add(*args, **kwargs):
        notification_threads.append(threading.get_ident())
        return original_add(*args, **kwargs)

    monkeypatch.setattr(scheduler, "add_notification", checked_add)
    monkeypatch.setattr(note_routes, "_scheduler_ref", scheduler)

    message, ok = await builtin_actions.action_check_email_urgency("alice")

    assert ok is True
    assert "notified 1" in message
    assert synthesis_loops == [expected_loop]
    assert notification_threads == [expected_thread]
    notifications = scheduler.pop_notifications(owner="alice")
    assert len(notifications) == 1
    assert notifications[0]["body"] == "Synthesized urgency reminder."


@pytest.mark.asyncio
async def test_action_cancellation_rolls_back_without_checkpoint(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, _runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )
    state_path = tmp_path / "email_urgency_state_alice.json"
    state_path.write_text(
        json.dumps({"owner": "alice", "per_uid": {}, "notified_uids": []}),
        encoding="utf-8",
    )
    entered = threading.Event()
    release = threading.Event()
    dispatch_cancelled = threading.Event()

    async def blocked_dispatch(**_kwargs):
        entered.set()
        try:
            while not release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            dispatch_cancelled.set()
            raise
        return {
            "browser_sent": True,
            "email_sent": False,
            "ntfy_sent": False,
            "webhook_sent": False,
        }

    monkeypatch.setattr(note_routes, "dispatch_reminder", blocked_dispatch)
    task = asyncio.create_task(
        builtin_actions.action_check_email_urgency("alice")
    )
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.sleep(0.1)

    assert dispatch_cancelled.is_set()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "owner": "alice",
        "per_uid": {},
        "notified_uids": [],
    }


@pytest.mark.asyncio
async def test_account_scoped_actions_merge_disjoint_checkpoints(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )
    deliveries = []

    async def delivered(**kwargs):
        deliveries.append(kwargs["note_body"])
        return {
            "browser_sent": True,
            "email_sent": False,
            "ntfy_sent": False,
            "webhook_sent": False,
        }

    monkeypatch.setattr(note_routes, "dispatch_reminder", delivered)

    await builtin_actions.action_check_email_urgency(
        "alice", prompt='{"account_id":"acct-a"}'
    )
    runtime["accounts"] = ["acct-b"]
    await builtin_actions.action_check_email_urgency(
        "alice", prompt='{"account_id":"acct-b"}'
    )
    runtime["accounts"] = ["acct-a"]
    await builtin_actions.action_check_email_urgency(
        "alice", prompt='{"account_id":"acct-a"}'
    )

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert len(deliveries) == 2
    assert "Urgent request for acct-a" in deliveries[0]
    assert "Urgent request for acct-b" in deliveries[1]
    assert state["notified_uids"] == ["acct-a:1", "acct-b:1"]
    assert set(state["per_uid"]) == {"acct-a:1", "acct-b:1"}
    assert state["total_unread"] == 2
    assert state["total_urgent"] == 2


@pytest.mark.asyncio
async def test_failed_account_scan_preserves_checkpoint_until_recovery(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a", "acct-b"]
    )
    deliveries = []

    async def delivered(**kwargs):
        deliveries.append(kwargs["note_body"])
        return {
            "browser_sent": True,
            "email_sent": False,
            "ntfy_sent": False,
            "webhook_sent": False,
        }

    monkeypatch.setattr(note_routes, "dispatch_reminder", delivered)

    await builtin_actions.action_check_email_urgency("alice")
    runtime["failures"] = {"acct-b"}
    await builtin_actions.action_check_email_urgency("alice")

    failed_state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert failed_state["notified_uids"] == ["acct-a:1", "acct-b:1"]
    assert set(failed_state["per_uid"]) == {"acct-a:1", "acct-b:1"}

    runtime["failures"] = set()
    runtime["accounts"] = ["acct-b"]
    await builtin_actions.action_check_email_urgency(
        "alice", prompt='{"account_id":"acct-b"}'
    )

    assert len(deliveries) == 1


@pytest.mark.asyncio
async def test_cached_flags_refresh_prunes_checkpoint_after_message_is_read(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )

    async def delivered(**_kwargs):
        return {
            "browser_sent": True,
            "email_sent": False,
            "ntfy_sent": False,
            "webhook_sent": False,
        }

    monkeypatch.setattr(note_routes, "dispatch_reminder", delivered)
    await builtin_actions.action_check_email_urgency("alice")
    runtime["seen_accounts"] = {"acct-a"}
    await builtin_actions.action_check_email_urgency("alice")

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert state["notified_uids"] == []
    assert state["per_uid"]["acct-a:1"]["unread"] is False
    assert state["total_unread"] == 0
