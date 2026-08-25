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
    def __init__(self, account_id, failures, seen_accounts, search_uids):
        self.account_id = account_id
        self.failures = failures
        self.seen_accounts = seen_accounts
        self.search_uids = tuple(search_uids.get(account_id, ("1",)))

    def select(self, *_args, **_kwargs):
        if self.account_id in self.failures:
            raise RuntimeError(f"{self.account_id} unavailable")
        return "OK", []

    def uid(self, command, *_args):
        if self.account_id in self.failures:
            raise RuntimeError(f"{self.account_id} unavailable")
        if command == "SEARCH":
            return "OK", [" ".join(self.search_uids).encode()]
        uid = _args[0]
        uid = uid.decode() if isinstance(uid, bytes) else str(uid)
        query = str(_args[-1]) if _args else ""
        seen = "\\Seen" if self.account_id in self.seen_accounts else ""
        flags = f"{uid} (UID {uid} FLAGS ({seen}))".encode()
        if query == "(UID FLAGS)":
            return "OK", [flags]
        raw = (
            f"From: Sender {self.account_id} <sender-{self.account_id}@example.com>\r\n"
            f"Subject: Urgent request for {self.account_id} uid {uid}\r\n"
            f"Message-ID: <{self.account_id}-{uid}@example.com>\r\n"
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
        "search_uids": {},
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
            str(account_id),
            runtime["failures"],
            runtime["seen_accounts"],
            runtime["search_uids"],
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


def test_stale_complete_scan_preserves_newer_state_and_discards_stale_only_facts():
    from src.builtin_actions import _merge_email_urgency_state

    prior = {
        "owner": "alice",
        "per_uid": {
            "acct:1": {"score": 0, "unread": True, "reason": "newer"},
            "acct:2": {"score": 2, "unread": True, "reason": "new UID"},
        },
        "notified_uids": ["acct:2"],
        "account_generations": {
            "acct": {"checkpoint": 1, "complete": 1},
        },
    }

    merged = _merge_email_urgency_state(
        prior,
        owner="alice",
        per_uid_scores={
            "acct:1": {"score": 0, "unread": False, "reason": "older"},
            "acct:3": {"score": 3, "unread": True, "reason": "also observed"},
        },
        notified_uids={"acct:1", "acct:2", "acct:3"},
        all_unread_keys={"acct:3"},
        fully_scanned_account_ids={"acct"},
        base_account_generations={
            "acct": {"checkpoint": 0, "complete": 0},
        },
        timestamp=300.0,
    )

    assert merged["per_uid"]["acct:1"]["reason"] == "newer"
    assert set(merged["per_uid"]) == {"acct:1", "acct:2"}
    assert merged["notified_uids"] == ["acct:2"]
    assert merged["account_generations"]["acct"] == {
        "checkpoint": 1,
        "complete": 1,
    }


def test_newer_partial_checkpoint_fences_older_complete_snapshot():
    from src.builtin_actions import _merge_email_urgency_state

    legacy = {
        "owner": "alice",
        "per_uid": {
            "acct:1": {"score": 3, "unread": True},
        },
        "notified_uids": ["acct:1"],
    }
    partial = _merge_email_urgency_state(
        legacy,
        owner="alice",
        per_uid_scores={
            "acct:2": {"score": 3, "unread": True},
        },
        notified_uids={"acct:1", "acct:2"},
        all_unread_keys={"acct:2"},
        fully_scanned_account_ids=set(),
        base_account_generations={},
        timestamp=200.0,
    )
    assert partial["account_generations"]["acct"] == {
        "checkpoint": 1,
        "complete": 0,
    }

    merged = _merge_email_urgency_state(
        partial,
        owner="alice",
        per_uid_scores={
            "acct:1": {"score": 3, "unread": True},
        },
        notified_uids={"acct:1", "acct:2"},
        all_unread_keys={"acct:1"},
        fully_scanned_account_ids={"acct"},
        base_account_generations={},
        timestamp=300.0,
    )

    assert set(merged["per_uid"]) == {"acct:1", "acct:2"}
    assert merged["notified_uids"] == ["acct:1", "acct:2"]
    assert merged["account_generations"]["acct"] == {
        "checkpoint": 1,
        "complete": 0,
    }


def test_authoritative_retirement_prunes_payload_and_recomputes_api_totals():
    from src.builtin_actions import _merge_email_urgency_state

    prior = {
        "owner": "alice",
        "per_uid": {
            "acct-a:1": {"score": 1, "unread": True},
            "acct-b:1": {"score": 3, "unread": True},
        },
        "notified_uids": ["acct-b:1"],
        "account_generations": {
            "acct-a": {"checkpoint": 1, "complete": 1},
            "acct-b": {"checkpoint": 1, "complete": 1},
        },
    }

    merged = _merge_email_urgency_state(
        prior,
        owner="alice",
        per_uid_scores={},
        notified_uids=prior["notified_uids"],
        all_unread_keys=set(),
        fully_scanned_account_ids=set(),
        base_account_generations=prior["account_generations"],
        timestamp=300.0,
        retired_account_ids={"acct-b"},
        base_payload_account_ids={"acct-a", "acct-b"},
    )

    assert merged["per_uid"] == {
        "acct-a:1": {"score": 1, "unread": True},
    }
    assert merged["notified_uids"] == []
    assert merged["total_unread"] == 1
    assert merged["total_urgent"] == 0
    assert merged["max_score"] == 1
    assert merged["account_generations"]["acct-b"] == {
        "checkpoint": 2,
        "complete": 1,
    }


def test_authoritative_retirement_respects_generation_and_membership_fences():
    from src.builtin_actions import _merge_email_urgency_state

    newer = {
        "owner": "alice",
        "per_uid": {
            "acct-b:2": {"score": 3, "unread": True, "reason": "newer"},
        },
        "notified_uids": ["acct-b:2"],
        "account_generations": {
            "acct-b": {"checkpoint": 2, "complete": 2},
        },
    }
    generation_fenced = _merge_email_urgency_state(
        newer,
        owner="alice",
        per_uid_scores={},
        notified_uids=newer["notified_uids"],
        all_unread_keys=set(),
        fully_scanned_account_ids=set(),
        base_account_generations={
            "acct-b": {"checkpoint": 1, "complete": 1},
        },
        timestamp=300.0,
        retired_account_ids={"acct-b"},
        base_payload_account_ids={"acct-b"},
    )
    assert generation_fenced["per_uid"] == newer["per_uid"]
    assert generation_fenced["notified_uids"] == ["acct-b:2"]
    assert generation_fenced["account_generations"]["acct-b"] == {
        "checkpoint": 2,
        "complete": 2,
    }

    legacy_first_write = {
        "owner": "alice",
        "per_uid": {
            "acct-b:3": {"score": 2, "unread": True, "reason": "concurrent"},
        },
        "notified_uids": [],
    }
    membership_fenced = _merge_email_urgency_state(
        legacy_first_write,
        owner="alice",
        per_uid_scores={},
        notified_uids=[],
        all_unread_keys=set(),
        fully_scanned_account_ids=set(),
        base_account_generations={},
        timestamp=300.0,
        retired_account_ids={"acct-b"},
        base_payload_account_ids=set(),
    )
    assert membership_fenced["per_uid"] == legacy_first_write["per_uid"]


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
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["owner"] == "alice"
    assert state["per_uid"] == {}
    assert state["notified_uids"] == []
    # The pre-scan active marker is not a delivered/checkpointed UID. It must
    # survive cancellation so a concurrent deletion cleanup can fence this
    # first-ever account scan.
    assert state["account_generations"] == {
        "acct-a": {"checkpoint": 0, "complete": 0},
    }


@pytest.mark.asyncio
async def test_first_scan_revalidates_account_deleted_before_registration(
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
    original_transaction = builtin_actions._run_email_urgency_state_transaction

    async def delete_before_registration(state_path, lock_db_path, operation):
        if operation.__name__ == "_register_accounts":
            # The initial DB read saw the account, but deletion commits before
            # its first active marker. The post-registration enumeration must
            # observe that absence and retire the marker without touching IMAP.
            runtime["accounts"] = []
        return await original_transaction(state_path, lock_db_path, operation)

    monkeypatch.setattr(
        builtin_actions,
        "_run_email_urgency_state_transaction",
        delete_before_registration,
    )

    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert deliveries == []
    assert state["per_uid"] == {}
    assert state["notified_uids"] == []
    assert state["account_generations"]["acct-a"] == {
        "checkpoint": 1,
        "complete": 0,
    }


@pytest.mark.asyncio
async def test_first_scan_deleted_after_revalidation_is_fenced_by_cleanup_pass(
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
    original_transaction = builtin_actions._run_email_urgency_state_transaction
    scanned = asyncio.Event()
    release_scan = asyncio.Event()
    paused = False

    async def pause_first_delivery(state_path, lock_db_path, operation):
        nonlocal paused
        if operation.__name__ == "_dispatch_and_checkpoint" and not paused:
            paused = True
            scanned.set()
            await release_scan.wait()
        return await original_transaction(state_path, lock_db_path, operation)

    monkeypatch.setattr(
        builtin_actions,
        "_run_email_urgency_state_transaction",
        pause_first_delivery,
    )

    stale = asyncio.create_task(
        builtin_actions.action_check_email_urgency("alice")
    )
    await asyncio.wait_for(scanned.wait(), timeout=2)
    registered = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert registered["account_generations"]["acct-a"] == {
        "checkpoint": 0,
        "complete": 0,
    }

    # Public-action boundary: deletion itself does not mutate urgency state.
    # The authoritative zero-account pass is what advances the active marker
    # before the paused scan reaches any reminder channel.
    runtime["accounts"] = []
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")
    release_scan.set()
    await asyncio.wait_for(stale, timeout=2)

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert deliveries == []
    assert state["per_uid"] == {}
    assert state["notified_uids"] == []
    assert state["total_unread"] == 0
    assert state["total_urgent"] == 0
    assert state["account_generations"]["acct-a"] == {
        "checkpoint": 1,
        "complete": 0,
    }


def test_scan_keeps_registration_generation_when_cleanup_precedes_basis(
    monkeypatch,
    tmp_path,
):
    """Cleanup after verification cannot become the stale scan's baseline."""
    from core import database
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
    verified_account_selected = threading.Event()
    release_verified_account = threading.Event()
    stale_query_count = 0
    query_count_lock = threading.Lock()

    class _PausingQuery(_Query):
        def all(self):
            nonlocal stale_query_count
            rows = list(self._rows)
            if threading.current_thread().name == "stale-urgency-scan":
                with query_count_lock:
                    stale_query_count += 1
                    should_pause = stale_query_count == 2
                if should_pause:
                    # The second enumeration has selected the enabled row, but
                    # the action has not yet retained/used its checkpoint basis.
                    verified_account_selected.set()
                    assert release_verified_account.wait(5)
            return rows

    class _PausingDb(_Db):
        def query(self, _model):
            return _PausingQuery(self._rows())

    monkeypatch.setattr(
        database,
        "SessionLocal",
        lambda: _PausingDb(
            lambda: [_account(value) for value in runtime["accounts"]]
        ),
    )

    stale_result = {}

    def run_stale_scan():
        try:
            stale_result["value"] = asyncio.run(
                builtin_actions.action_check_email_urgency("alice")
            )
        except BaseException as exc:
            stale_result["error"] = exc

    worker = threading.Thread(
        target=run_stale_scan,
        name="stale-urgency-scan",
    )
    worker.start()
    assert verified_account_selected.wait(5)

    runtime["accounts"] = []
    with pytest.raises(builtin_actions.TaskNoop):
        asyncio.run(builtin_actions.action_check_email_urgency("alice"))
    state_path = tmp_path / "email_urgency_state_alice.json"
    retired = json.loads(state_path.read_text(encoding="utf-8"))
    assert retired["account_generations"]["acct-a"] == {
        "checkpoint": 1,
        "complete": 0,
    }

    release_verified_account.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert "error" not in stale_result

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert deliveries == []
    assert state["per_uid"] == {}
    assert state["notified_uids"] == []
    assert state["account_generations"]["acct-a"] == {
        "checkpoint": 1,
        "complete": 0,
    }


@pytest.mark.asyncio
async def test_payload_empty_tombstone_fences_reenable_redelete_and_can_recover(
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
    await builtin_actions.action_check_email_urgency("alice")
    assert len(deliveries) == 1

    runtime["accounts"] = []
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")
    first_tombstone = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert first_tombstone["per_uid"] == {}
    assert first_tombstone["account_generations"]["acct-a"] == {
        "checkpoint": 2,
        "complete": 1,
    }

    original_transaction = builtin_actions._run_email_urgency_state_transaction
    scanned = asyncio.Event()
    release_scan = asyncio.Event()
    pause_reenabled = True

    async def pause_reenabled_delivery(state_path, lock_db_path, operation):
        nonlocal pause_reenabled
        if operation.__name__ == "_dispatch_and_checkpoint" and pause_reenabled:
            pause_reenabled = False
            scanned.set()
            await release_scan.wait()
        return await original_transaction(state_path, lock_db_path, operation)

    monkeypatch.setattr(
        builtin_actions,
        "_run_email_urgency_state_transaction",
        pause_reenabled_delivery,
    )
    deliveries.clear()
    runtime["accounts"] = ["acct-a"]
    stale_reenabled = asyncio.create_task(
        builtin_actions.action_check_email_urgency("alice")
    )
    await asyncio.wait_for(scanned.wait(), timeout=2)

    # Delete/disable again while the re-enabled scan is based on checkpoint 2.
    # Discovery must include the payload-empty generation tombstone and advance
    # it, otherwise the paused scan would deliver and resurrect acct-a:1.
    runtime["accounts"] = []
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")
    release_scan.set()
    await asyncio.wait_for(stale_reenabled, timeout=2)

    fenced = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert deliveries == []
    assert fenced["per_uid"] == {}
    assert fenced["notified_uids"] == []
    assert fenced["account_generations"]["acct-a"] == {
        "checkpoint": 3,
        "complete": 1,
    }

    # A later authoritative pass advances the empty tombstone again, fencing
    # any scan that captured checkpoint 3 before this absence was confirmed.
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")
    repeated = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert repeated["account_generations"]["acct-a"] == {
        "checkpoint": 4,
        "complete": 1,
    }

    # Re-enabling after the latest tombstone captures checkpoint 4 and can
    # publish fresh payload normally.
    runtime["accounts"] = ["acct-a"]
    await builtin_actions.action_check_email_urgency("alice")
    recovered = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert len(deliveries) == 1
    assert set(recovered["per_uid"]) == {"acct-a:1"}
    assert recovered["notified_uids"] == ["acct-a:1"]
    assert recovered["account_generations"]["acct-a"] == {
        "checkpoint": 5,
        "complete": 2,
    }


@pytest.mark.parametrize(
    ("stale_uids", "newer_uids"),
    [
        (["1"], ["1", "2"]),
        (["1", "2"], ["1"]),
    ],
    ids=["preserve-newer-addition", "reject-stale-only-delivery"],
)
@pytest.mark.asyncio
async def test_stale_same_account_scan_cannot_override_newer_commit(
    monkeypatch,
    tmp_path,
    stale_uids,
    newer_uids,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a"]
    )
    runtime["search_uids"]["acct-a"] = stale_uids
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
    original_transaction = builtin_actions._run_email_urgency_state_transaction
    stale_scan_ready = asyncio.Event()
    release_stale_scan = asyncio.Event()
    transaction_count = 0

    async def order_transactions(state_path, lock_db_path, operation):
        nonlocal transaction_count
        if operation.__name__ == "_dispatch_and_checkpoint":
            transaction_count += 1
            if transaction_count == 1:
                stale_scan_ready.set()
                await release_stale_scan.wait()
        return await original_transaction(state_path, lock_db_path, operation)

    monkeypatch.setattr(
        builtin_actions,
        "_run_email_urgency_state_transaction",
        order_transactions,
    )

    stale = asyncio.create_task(
        builtin_actions.action_check_email_urgency("alice")
    )
    await asyncio.wait_for(stale_scan_ready.wait(), timeout=2)

    runtime["search_uids"]["acct-a"] = newer_uids
    newer_result = await asyncio.wait_for(
        builtin_actions.action_check_email_urgency("alice"),
        timeout=2,
    )
    release_stale_scan.set()
    stale_result = await asyncio.wait_for(stale, timeout=2)

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert newer_result[1] is True
    assert stale_result[1] is True
    assert len(deliveries) == 1
    for uid in newer_uids:
        assert f"uid {uid}" in deliveries[0]
    for uid in set(stale_uids) - set(newer_uids):
        assert f"uid {uid}" not in deliveries[0]
    expected_keys = {f"acct-a:{uid}" for uid in newer_uids}
    assert set(state["per_uid"]) == expected_keys
    assert state["notified_uids"] == sorted(expected_keys)
    assert state["account_generations"]["acct-a"] == {
        "checkpoint": 1,
        "complete": 1,
    }


@pytest.mark.asyncio
async def test_mixed_stale_and_fresh_accounts_exclude_stale_rows_from_delivery(
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
    original_transaction = builtin_actions._run_email_urgency_state_transaction
    stale_scan_ready = asyncio.Event()
    release_stale_scan = asyncio.Event()
    transaction_count = 0

    async def order_transactions(state_path, lock_db_path, operation):
        nonlocal transaction_count
        if operation.__name__ == "_dispatch_and_checkpoint":
            transaction_count += 1
            if transaction_count == 1:
                stale_scan_ready.set()
                await release_stale_scan.wait()
        return await original_transaction(state_path, lock_db_path, operation)

    monkeypatch.setattr(
        builtin_actions,
        "_run_email_urgency_state_transaction",
        order_transactions,
    )

    stale = asyncio.create_task(
        builtin_actions.action_check_email_urgency("alice")
    )
    await asyncio.wait_for(stale_scan_ready.wait(), timeout=2)

    runtime["accounts"] = ["acct-a"]
    await asyncio.wait_for(
        builtin_actions.action_check_email_urgency(
            "alice",
            prompt='{"account_id":"acct-a"}',
        ),
        timeout=2,
    )
    release_stale_scan.set()
    await asyncio.wait_for(stale, timeout=2)

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert len(deliveries) == 2
    assert "acct-a" in deliveries[0]
    assert "acct-b" not in deliveries[0]
    assert "acct-b" in deliveries[1]
    assert "acct-a" not in deliveries[1]
    assert set(state["per_uid"]) == {"acct-a:1", "acct-b:1"}
    assert state["notified_uids"] == ["acct-a:1", "acct-b:1"]


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
async def test_full_enumeration_retires_deleted_or_disabled_account(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a", "acct-b"]
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

    # The production query returns only enabled, owner-visible accounts. A
    # deleted row and a disabled row are therefore the same authoritative
    # absence at this boundary.
    runtime["accounts"] = ["acct-a"]
    await builtin_actions.action_check_email_urgency("alice")

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert set(state["per_uid"]) == {"acct-a:1"}
    assert state["notified_uids"] == ["acct-a:1"]
    assert state["total_unread"] == 1
    assert state["total_urgent"] == 1
    assert state["max_score"] == 3
    assert state["account_generations"]["acct-b"] == {
        "checkpoint": 2,
        "complete": 1,
    }


@pytest.mark.asyncio
async def test_zero_enabled_accounts_cleanup_precedes_model_resolution(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes
    from src import task_endpoint

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
    runtime["accounts"] = []

    model_resolution_owners = []

    def no_model_available(*_args, **kwargs):
        model_resolution_owners.append(kwargs.get("owner"))
        return []

    monkeypatch.setattr(
        task_endpoint,
        "resolve_task_candidates",
        no_model_available,
    )
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency("alice")
    assert model_resolution_owners == ["alice"]

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert state["per_uid"] == {}
    assert state["notified_uids"] == []
    assert state["total_unread"] == 0
    assert state["total_urgent"] == 0
    assert state["max_score"] == 0
    assert state["account_generations"]["acct-a"] == {
        "checkpoint": 2,
        "complete": 1,
    }


@pytest.mark.asyncio
async def test_scoped_missing_account_retires_only_selected_payload(
    monkeypatch,
    tmp_path,
):
    import routes.note_routes as note_routes

    builtin_actions, runtime = _configure_action(
        monkeypatch, tmp_path, ["acct-a", "acct-b"]
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

    # A production account-id filter returns no row when the selected account
    # was deleted or disabled. Other accounts are outside this scoped query and
    # must remain untouched.
    runtime["accounts"] = []
    with pytest.raises(builtin_actions.TaskNoop):
        await builtin_actions.action_check_email_urgency(
            "alice",
            prompt='{"account_id":"acct-b"}',
        )

    state = json.loads(
        (tmp_path / "email_urgency_state_alice.json").read_text(encoding="utf-8")
    )
    assert set(state["per_uid"]) == {"acct-a:1"}
    assert state["notified_uids"] == ["acct-a:1"]
    assert state["total_unread"] == 1
    assert state["total_urgent"] == 1
    assert state["account_generations"]["acct-b"] == {
        "checkpoint": 2,
        "complete": 1,
    }


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
    assert failed_state["total_unread"] == 2
    assert failed_state["total_urgent"] == 2
    assert failed_state["account_generations"]["acct-b"] == {
        "checkpoint": 1,
        "complete": 1,
    }

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
