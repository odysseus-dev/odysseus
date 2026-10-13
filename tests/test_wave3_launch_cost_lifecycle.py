"""Structural dispatch cost and exact publication lifetime regressions."""
import asyncio
from dataclasses import replace
import json
import os
import time

import pytest
from core.atomic_io import atomic_write_json
from src import bg_jobs, containment, process_ownership
from src.agent_runtime import resources as identities
from src.agent_runtime.authority import ExactOperation, bind_request_authority
from src.agent_runtime.resources import NativeBackendResource, ResourceIdentityError
from src.agent_tools.subprocess_tools import BashTool
from src.process_lifecycle import ProcessIdentity
from tests.test_runtime_resource_integration import workspace, authority, dispatch
from tests.test_background_resource_identity import seed
from src.agent_runtime import process_resources as resources


@pytest.mark.parametrize('tool,content', [('bash', 'printf guarded'), ('python', 'print("guarded")')])
async def test_real_dispatch_scans_workspace_once_per_binding(workspace, monkeypatch, tool, content):
    (workspace / 'child').mkdir()
    (workspace / 'child' / 'link').symlink_to(workspace / 'child')
    calls = []
    walk = os.walk
    def counted(*args, **kwargs):
        calls.append(args[0])
        return walk(*args, **kwargs)
    monkeypatch.setattr(os, 'walk', counted)
    admitted = authority(workspace, tool)
    for _ in range(2):
        calls.clear()
        _, result = await dispatch(admitted, tool, content)
        assert result['exit_code'] == 0, result
        assert calls == [workspace]
        assert not list(resources._LAUNCH_DIR.glob('*.json'))


async def test_alias_created_after_resolution_is_denied_at_binding(workspace):
    admitted = authority(workspace)
    op = ExactOperation.normalize('bash', 'printf safe')
    bound = resources.resolve_process_operation(admitted, op, NativeBackendResource('bash'))
    resources._LAUNCH_DIR.mkdir(parents=True)
    state = resources._LAUNCH_DIR / ('a' * 32 + '.json')
    state.write_text('{}')
    (workspace / 'alias').symlink_to(state)
    with bind_request_authority(admitted), pytest.raises(ResourceIdentityError):
        with resources.bind_process_operation(bound):
            pytest.fail('New control-plane alias admitted')


async def test_publication_retained_during_launch_and_retired_after_teardown(workspace, monkeypatch):
    entered, resume = asyncio.Event(), asyncio.Event()
    run = containment.run
    paths = []
    async def held(grant, command, **kwargs):
        launch = resources.active_process_operation().launch
        path = resources.launch_path(launch.generation)
        assert path.is_file()
        paths.append(path)
        entered.set()
        await resume.wait()
        return await run(grant, command, **kwargs)
    monkeypatch.setattr(containment, 'run', held)
    task = asyncio.create_task(dispatch(authority(workspace), 'bash', 'printf foreground'))
    await asyncio.wait_for(entered.wait(), 5)
    assert paths[0].is_file()
    resume.set()
    _, result = await task
    assert result['exit_code'] == 0 and result['teardown']['dead']
    assert not paths[0].exists()


async def test_retired_publication_cannot_replay_bound_reservation(workspace):
    admitted = authority(workspace)
    op = ExactOperation.normalize('bash', 'printf once')
    bound = resources.resolve_process_operation(admitted, op, NativeBackendResource('bash'))
    from src import tool_execution
    token = tool_execution._active_workspace.set(str(workspace))
    try:
        with bind_request_authority(admitted), resources.bind_process_operation(bound):
            ctx = {'owner': 'alice', 'session_id': 'thread'}
            first = await BashTool().execute(op.input, ctx)
            assert first['exit_code'] == 0
            assert not resources.launch_path(bound.launch.generation).exists()
            second = await BashTool().execute(op.input, ctx)
            assert second['failure_kind'] == 'resource_identity_denied'
        copy = replace(bound, exact_approval=None)
        with pytest.raises(ResourceIdentityError):
            with resources.bind_process_operation(copy):
                pytest.fail('Approval copy renewed a consumed launch')
    finally:
        tool_execution._active_workspace.reset(token)


@pytest.mark.parametrize('publication', [[], None, 'malformed'])
def test_nonobject_publication_cannot_be_retired(workspace, publication):
    resource, rec = seed(workspace, status='done')
    path = resources.launch_path(resource.generation)
    path.write_text(json.dumps(publication))
    launch = identities.ProcessLaunchResource.from_dict(rec['launch_resource'])
    assert not resources.retire_launch(launch, resource.containment_id, job=resource)
    assert json.loads(path.read_text()) == publication


async def test_corrupt_publication_retirement_preserves_command_result(workspace, monkeypatch):
    attach = resources.attach_containment_processes
    paths = []
    def corrupt_after_attachment(launch, containment_id):
        observed = attach(launch, containment_id)
        path = resources.launch_path(launch.generation)
        path.write_text('[]')
        paths.append(path)
        return observed
    monkeypatch.setattr(resources, 'attach_containment_processes', corrupt_after_attachment)
    _, result = await dispatch(authority(workspace), 'bash', 'printf completed')
    assert result['exit_code'] == 0 and result['output'] == 'completed', result
    assert paths[0].read_text() == '[]'


@pytest.mark.parametrize('status,followed_up,old,removed', [
    ('running', True, True, False), ('done', False, True, False),
    ('done', True, False, False), ('done', True, True, True), ('failed', True, True, True),
])
def test_background_publication_tracks_supported_history_lifetime(workspace, status, followed_up, old, removed):
    resource, rec = seed(workspace, status=status)
    rec.update(followed_up=followed_up, ended_at=time.time() - (bg_jobs._RETENTION_S + 10 if old else 0))
    jobs = {'job': rec}
    bg_jobs._save(jobs)
    assert resources.launch_path(resource.generation).exists()
    bg_jobs._prune(jobs, time.time())
    assert resources.launch_path(resource.generation).exists() is not removed
    assert ('job' not in jobs) is removed
    if removed:
        bg_jobs._save(jobs)
        with pytest.raises(ResourceIdentityError):
            resources.validate_job(resource)


def test_old_generation_retirement_cannot_delete_replacement(workspace):
    old, rec = seed(workspace, status='done')
    new, _ = seed(workspace, status='done')
    old_launch = identities.ProcessLaunchResource.from_dict(rec['launch_resource'])
    assert resources.retire_launch(old_launch, old.containment_id, job=old)
    assert resources.launch_path(new.generation).is_file()
    # Even a replaced file at the old generation's slot is not deletable by old linkage.
    replacement = json.loads(resources.launch_path(new.generation).read_text())
    atomic_write_json(resources.launch_path(old.generation), replacement)
    assert not resources.retire_launch(old_launch, old.containment_id, job=old)
    assert resources.launch_path(old.generation).is_file()


@pytest.mark.parametrize('manager,release,retired', [
    (process_ownership.OWNED, False, False), (process_ownership.UNVERIFIABLE, False, False),
    (process_ownership.OWNED, True, False), (process_ownership.UNVERIFIABLE, True, False),
    (process_ownership.GONE, False, True), (process_ownership.FOREIGN, False, True),
    (process_ownership.GONE, True, True),
])
def test_startup_retirement_does_not_invent_process_death(workspace, monkeypatch, manager, release, retired):
    admitted = authority(workspace)
    launch = resources.resolve_process_operation(admitted, ExactOperation.normalize('bash', 'printf recovery'), NativeBackendResource('bash')).launch
    cid = 'receipt'
    resources.publish_launch(launch, admitted, cid)
    atomic_write_json(containment._store_path(), {cid: {'id': cid, 'launch_generation': launch.generation,
        'manager_pid': 123, 'manager_token': 'old-manager', 'release': {'dead': release}}})
    monkeypatch.setattr(process_ownership, 'verify', lambda *args: manager)
    assert resources.prune_foreground_publications() == int(retired)
    assert resources.launch_path(launch.generation).exists() is not retired
    assert containment._load_records()[cid]['release']['dead'] is release


def test_restart_never_prunes_background_linkage(workspace, monkeypatch):
    resource, _ = seed(workspace, status='done')
    monkeypatch.setattr(process_ownership, 'verify', lambda *args: process_ownership.GONE)
    assert resources.prune_foreground_publications() == 0
    assert resources.launch_path(resource.generation).is_file()


def test_snapshot_is_rebuilt_for_each_guard(workspace):
    resources._LAUNCH_DIR.mkdir(parents=True)
    target = workspace / 'data'; target.write_text('ordinary')
    (workspace / 'link').symlink_to(target)
    resources.guard_launch_workspace(identities.FilesystemRoot.seal(workspace))
    os.link(target, resources._LAUNCH_DIR / ('b' * 32 + '.json'))
    with pytest.raises(ResourceIdentityError):
        resources.guard_launch_workspace(identities.FilesystemRoot.seal(workspace))


def test_missing_receipt_publication_cannot_recover_authority(workspace):
    admitted = authority(workspace)
    launch = resources.resolve_process_operation(admitted, ExactOperation.normalize('bash', 'printf recovery'), NativeBackendResource('bash')).launch
    resources.publish_launch(launch, admitted, 'missing-receipt')
    assert resources.prune_foreground_publications() == 1
    assert not resources.launch_path(launch.generation).exists()
    assert not containment._load_records()


@pytest.mark.parametrize('receipt_data', ['{corrupt', '[]', '{"receipt":null}'])
def test_unreadable_receipts_cannot_retire_live_consumers(workspace, receipt_data):
    admitted = authority(workspace)
    launch = resources.resolve_process_operation(admitted, ExactOperation.normalize('bash', 'printf pending'), NativeBackendResource('bash')).launch
    resources.publish_launch(launch, admitted, 'receipt')
    containment._store_path().write_text(receipt_data)
    assert resources.prune_foreground_publications() == 0
    assert resources.launch_path(launch.generation).is_file()


def test_missing_manager_identity_cannot_retire_attachment(workspace, monkeypatch):
    admitted = authority(workspace)
    launch = resources.resolve_process_operation(admitted, ExactOperation.normalize('bash', 'printf pending'), NativeBackendResource('bash')).launch
    resources.publish_launch(launch, admitted, 'receipt')
    atomic_write_json(containment._store_path(), {'receipt': {'id': 'receipt',
        'launch_generation': launch.generation, 'release': {'dead': True}}})
    monkeypatch.setattr(process_ownership, 'verify', lambda *args: pytest.fail('Missing manager treated as observed'))
    assert resources.prune_foreground_publications() == 0
    assert resources.launch_path(launch.generation).is_file()
