"""manage_tasks mutations must fail closed on owner-less / cross-owner tasks.

The edit/delete/pause/run actions of ``do_manage_tasks`` previously gated with
``if owner and task.owner and task.owner != owner``. The middle term made the
check a no-op whenever the task had no owner — the state a scheduled task is in
when it was created in no-login mode (or via the localhost middleware bypass)
before the periodic legacy-owner sweep reassigns it to the admin user. So any
authenticated user's agent could edit, delete, pause, or *run* another tenant's
owner-less task. The sibling ``list`` action already scopes with an exact
``ScheduledTask.owner == owner`` filter, so the mutators were strictly more
permissive than the reader.
"""

import json
import sys
from datetime import datetime

import pytest

from tests.helpers.database import disposable_database
from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
from core.database import ScheduledTask
from src.tools.system import do_manage_tasks


@pytest.fixture(autouse=True)
def _task_database(tmp_path):
    # do_manage_tasks imports SessionLocal at call time. Own this binding for
    # just one test, including helpers that seed and inspect its rows.
    with disposable_database(tmp_path) as factory:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(sys.modules[__name__], "_TS", factory, raising=False)
            patcher.setattr(cdb, "SessionLocal", factory)
            yield


def _seed(task_id, owner, *, name=None):
    db = _TS()
    try:
        db.add(ScheduledTask(
            id=task_id, owner=owner, name=name or task_id, prompt="original",
            task_type="llm", trigger_type="webhook", status="active",
            output_target="session",
        ))
        db.commit()
    finally:
        db.close()


def _get(task_id):
    db = _TS()
    try:
        return db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
    finally:
        db.close()


def _delete(task_id):
    db = _TS()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if task:
            db.delete(task)
            db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_edit_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-edit", None)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "ownerless-edit", "prompt": "pwned"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-edit").prompt == "original"


@pytest.mark.asyncio
async def test_delete_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-del", None)
    out = await do_manage_tasks(
        json.dumps({"action": "delete", "task_id": "ownerless-del"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-del") is not None


@pytest.mark.asyncio
async def test_pause_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-pause", None)
    out = await do_manage_tasks(
        json.dumps({"action": "pause", "task_id": "ownerless-pause"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("ownerless-pause").status == "active"


@pytest.mark.asyncio
async def test_run_denied_on_ownerless_task_for_authenticated_user():
    _seed("ownerless-run", None)
    out = await do_manage_tasks(
        json.dumps({"action": "run", "task_id": "ownerless-run"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"


@pytest.mark.asyncio
async def test_edit_denied_on_other_owners_task():
    _seed("bob-task", "bob")
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "bob-task", "prompt": "pwned"}),
        owner="alice",
    )
    assert out["exit_code"] == 1 and out["error"] == "Access denied"
    assert _get("bob-task").prompt == "original"


@pytest.mark.asyncio
async def test_edit_allowed_for_matching_owner():
    _seed("alice-task", "alice")
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "alice-task", "prompt": "updated"}),
        owner="alice",
    )
    assert out["exit_code"] == 0
    assert _get("alice-task").prompt == "updated"


@pytest.mark.asyncio
async def test_edit_allowed_in_no_login_mode():
    # owner is None when auth is disabled — single-user mode keeps full access
    # to shared (owner-less) tasks, exactly as `list` returns them unfiltered.
    _seed("shared-task", None)
    out = await do_manage_tasks(
        json.dumps({"action": "edit", "task_id": "shared-task", "prompt": "updated"}),
        owner=None,
    )
    assert out["exit_code"] == 0
    assert _get("shared-task").prompt == "updated"


@pytest.mark.asyncio
async def test_mutation_resolves_exact_name_when_model_puts_name_in_task_id():
    _seed("task-uuid-for-name-fallback", "alice", name="Daily fixture by name")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "pause", "task_id": "Daily fixture by name"}),
            owner="alice",
        )
        assert out["exit_code"] == 0
        assert _get("task-uuid-for-name-fallback").status == "paused"
    finally:
        _delete("task-uuid-for-name-fallback")


@pytest.mark.asyncio
async def test_exact_name_fallback_rejects_ambiguous_matches():
    _seed("ambiguous-task-a", "alice", name="Duplicate task name")
    _seed("ambiguous-task-b", "alice", name="Duplicate task name")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "delete", "task_id": "Duplicate task name"}),
            owner="alice",
        )
        assert out["exit_code"] == 1
        assert out["error"] == "Task name 'Duplicate task name' matched 2 tasks; use task_id"
        assert _get("ambiguous-task-a") is not None
        assert _get("ambiguous-task-b") is not None
    finally:
        _delete("ambiguous-task-a")
        _delete("ambiguous-task-b")


@pytest.mark.asyncio
async def test_list_filters_by_exact_name_instead_of_dumping_all_tasks():
    _seed("list-filter-target", "alice", name="Needle task")
    _seed("list-filter-other", "alice", name="Other task")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "list", "name": "Needle task"}),
            owner="alice",
        )
        assert out["exit_code"] == 0
        assert "Needle task" in out["response"]
        assert "list-filter-target" in out["response"]
        assert "Other task" not in out["response"]
        assert "list-filter-other" not in out["response"]
    finally:
        _delete("list-filter-target")
        _delete("list-filter-other")


@pytest.mark.asyncio
async def test_list_filters_by_pattern_alias():
    _seed("pattern-filter-target", "alice", name="Pattern needle task")
    _seed("pattern-filter-other", "alice", name="Unrelated task")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "list", "pattern": "needle"}),
            owner="alice",
        )
        assert out["exit_code"] == 0
        assert "Pattern needle task" in out["response"]
        assert "pattern-filter-target" in out["response"]
        assert "Unrelated task" not in out["response"]
        assert "pattern-filter-other" not in out["response"]
    finally:
        _delete("pattern-filter-target")
        _delete("pattern-filter-other")


@pytest.mark.asyncio
async def test_list_filters_by_prompt_alias():
    _seed("prompt-filter-target", "alice", name="Prompt needle task")
    _seed("prompt-filter-other", "alice", name="Unrelated task")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "list", "prompt": "needle"}),
            owner="alice",
        )
        assert out["exit_code"] == 0
        assert "Prompt needle task" in out["response"]
        assert "prompt-filter-target" in out["response"]
        assert "Unrelated task" not in out["response"]
        assert "prompt-filter-other" not in out["response"]
    finally:
        _delete("prompt-filter-target")
        _delete("prompt-filter-other")


@pytest.mark.asyncio
async def test_one_off_create_persists_utc_date_and_next_run():
    result = await do_manage_tasks(json.dumps({
        'action': 'create', 'name': 'dated fixture', 'prompt': 'synthetic',
        'schedule': 'once', 'scheduled_date': '2099-01-01T09:00:00+09:00',
    }), owner='alice')
    assert result['exit_code'] == 0
    try:
        row = _get(result['task_id'])
        assert row.scheduled_date == datetime(2099, 1, 1)
        assert row.next_run == row.scheduled_date
    finally:
        _delete(result['task_id'])


@pytest.mark.asyncio
async def test_one_off_edit_pause_resume_keeps_exact_schedule():
    result = await do_manage_tasks(json.dumps({
        'action': 'create', 'name': 'dated edit fixture', 'prompt': 'synthetic',
        'schedule': 'once', 'scheduled_date': '2099-01-01T00:00:00Z',
    }), owner='alice')
    task_id = result['task_id']
    try:
        edited = await do_manage_tasks(json.dumps({
            'action': 'edit', 'task_id': task_id, 'scheduled_date': '2099-01-02T09:00:00+09:00',
        }), owner='alice')
        assert edited['exit_code'] == 0
        assert _get(task_id).scheduled_date == datetime(2099, 1, 2)
        assert _get(task_id).next_run == datetime(2099, 1, 2)
        for action in ['pause', 'resume']:
            out = await do_manage_tasks(json.dumps({'action': action, 'task_id': task_id}), owner='alice')
            assert out['exit_code'] == 0
        assert _get(task_id).status == 'active'
        assert _get(task_id).next_run == datetime(2099, 1, 2)
        invalid = await do_manage_tasks(json.dumps({
            'action': 'edit', 'task_id': task_id, 'name': 'must not stick', 'scheduled_date': 'bad-date',
        }), owner='alice')
        assert invalid['exit_code'] == 1
        assert _get(task_id).name == 'dated edit fixture'
        assert _get(task_id).scheduled_date == datetime(2099, 1, 2)
    finally:
        _delete(task_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, '', 'bad-date', '2000-01-01T00:00:00Z'])
async def test_invalid_one_off_date_never_creates_an_inert_active_task(value):
    out = await do_manage_tasks(json.dumps({
        'action': 'create', 'name': 'invalid dated fixture', 'prompt': 'synthetic',
        'schedule': 'once', 'scheduled_date': value,
    }), owner='alice')
    assert out['exit_code'] == 1
    with _TS() as db:
        assert db.query(ScheduledTask).filter(ScheduledTask.name == 'invalid dated fixture').count() == 0


@pytest.mark.asyncio
async def test_task_search_finds_instruction_text_without_other_owner_results():
    _seed('prompt-only-alice', 'alice', name='Reading fixture')
    _seed('prompt-only-bob', 'bob', name='Other private fixture')
    try:
        for task_id, owner in [('prompt-only-alice', 'alice'), ('prompt-only-bob', 'bob')]:
            out = await do_manage_tasks(json.dumps({
                'action': 'edit', 'task_id': task_id, 'prompt': 'Report the lavender inventory',
            }), owner=owner)
            assert out['exit_code'] == 0
        listed = await do_manage_tasks(json.dumps({'action': 'list', 'query': 'lavender'}), owner='alice')
        assert listed['exit_code'] == 0
        assert 'Reading fixture' in listed['response']
        assert 'Other private fixture' not in listed['response']
    finally:
        _delete('prompt-only-alice')
        _delete('prompt-only-bob')


@pytest.mark.asyncio
async def test_list_filters_by_match_alias():
    _seed("match-filter-target", "alice", name="Match needle task")
    _seed("match-filter-other", "alice", name="Unrelated task")
    try:
        out = await do_manage_tasks(
            json.dumps({"action": "list", "match": "needle"}),
            owner="alice",
        )
        assert out["exit_code"] == 0
        assert "Match needle task" in out["response"]
        assert "match-filter-target" in out["response"]
        assert "Unrelated task" not in out["response"]
        assert "match-filter-other" not in out["response"]
    finally:
        _delete("match-filter-target")
        _delete("match-filter-other")
