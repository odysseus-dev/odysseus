"""Permanent linkage loss suppresses continuation without granting authority."""
from types import SimpleNamespace
import time

import pytest
from src import bg_jobs, bg_monitor
from src.agent_runtime import process_resources as resources
from tests.test_background_resource_identity import store, seed
from src.agent_runtime.resources import ResourceIdentityError


@pytest.fixture
def monitor_session(monkeypatch):
    messages = []
    sess = SimpleNamespace(id='thread', owner='alice', model='test-model', get_context_messages=lambda: [])
    sm = SimpleNamespace(get_session=lambda sid: sess, add_message=lambda *args: messages.append(args), save_sessions=lambda: None)
    import src.ai_interaction as ai
    monkeypatch.setattr(ai, 'get_session_manager', lambda: sm)
    import src.agent_runs
    monkeypatch.setattr(src.agent_runs, 'is_active', lambda sid: False)
    async def drain(*args, **kwargs):
        messages.append('drained')
        return 'continued', []
    monkeypatch.setattr(bg_monitor, '_drain_agent', drain)
    return messages


@pytest.mark.parametrize('damage', ['missing', 'corrupt', 'wrong_owner', 'wrong_pid'])
async def test_invalid_linkage_is_terminal_without_message(store, monkeypatch, monitor_session, damage):
    resource, rec = seed(store, status='done')
    sidecar = bg_jobs._JOBS_DIR / 'job.authority.json'
    if damage == 'missing': sidecar.unlink()
    elif damage == 'corrupt': sidecar.write_text('{}')
    else:
        jobs = bg_jobs._load()
        if damage == 'wrong_owner': jobs['job']['resource_identity']['owner'] = 'bob'
        else: jobs['job']['pid'] = 99999
        bg_jobs._save(jobs)
        rec = jobs['job']
    # Invalid data must not even be rendered into a synthetic result message.
    monkeypatch.setattr(bg_monitor, '_background_result_message', lambda rec: pytest.fail('Invalid result rendered'))
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.TERMINAL_UNFOLLOWABLE
    assert not monitor_session
    assert not bg_jobs.pending_followups()
    assert bg_jobs.peek('job')['followup_state'] == 'terminal_unfollowable'
    assert not bg_jobs.peek('job').get('followed_up')
    with pytest.raises(ResourceIdentityError):
        resources.validate_job(resource)


async def test_busy_session_retries_then_continues(store, monkeypatch, monitor_session):
    _, rec = seed(store, status='done')
    import src.agent_runs
    monkeypatch.setattr(src.agent_runs, 'is_active', lambda sid: True)
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.RETRYABLE_LATER
    assert bg_jobs.pending_followups() and not monitor_session
    monkeypatch.setattr(src.agent_runs, 'is_active', lambda sid: False)
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.COMPLETED
    assert bg_jobs.peek('job')['followed_up']
    assert monitor_session and not bg_jobs.pending_followups()


async def test_terminal_record_prunes_exact_generation(store, monitor_session):
    resource, rec = seed(store, status='done')
    rec['ended_at'] = time.time() - bg_jobs._RETENTION_S - 10
    bg_jobs._save({'job': rec})
    (bg_jobs._JOBS_DIR / 'job.authority.json').unlink()
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.TERMINAL_UNFOLLOWABLE
    assert not bg_jobs.pending_followups()
    assert bg_jobs.peek('job') is None
    assert not resources.launch_path(resource.generation).exists()
    assert not monitor_session


def test_stale_terminal_snapshot_cannot_suppress_new_generation(store):
    _, old = seed(store, status='done')
    new, _ = seed(store, status='done')
    assert not bg_jobs.mark_unfollowable('job', expected_record=old)
    assert 'followup_state' not in bg_jobs.peek('job')
    assert resources.launch_path(new.generation).exists()


async def test_linkage_lost_during_continuation_cannot_deliver(store, monkeypatch, monitor_session):
    _, rec = seed(store, status='done')
    async def interrupted(*args, **kwargs):
        (bg_jobs._JOBS_DIR / 'job.authority.json').unlink()
        return 'must not be delivered', []
    monkeypatch.setattr(bg_monitor, '_drain_agent', interrupted)
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.TERMINAL_UNFOLLOWABLE
    assert not monitor_session
    assert not bg_jobs.pending_followups()


async def test_stale_terminal_outcome_retries_current_record(store, monkeypatch, monitor_session):
    _, old = seed(store, status='done')
    seed(store, status='done')
    async def terminal(rec): return bg_monitor.FollowupResult.TERMINAL_UNFOLLOWABLE
    monkeypatch.setattr(bg_monitor, '_run_followup', terminal)
    assert await bg_monitor._process_followup(old) is bg_monitor.FollowupResult.RETRYABLE_LATER
    assert bg_jobs.pending_followups()


@pytest.mark.parametrize('linkage', ['valid', 'damaged'])
async def test_deleted_session_settles_only_a_validated_launch(store, monkeypatch, monitor_session, linkage):
    """Wave 4: a job retired for a deleted session must not leave its launch effect RUNNING."""
    resource, rec = seed(store, status='done')
    if linkage == 'damaged':
        (bg_jobs._JOBS_DIR / 'job.authority.json').write_text('{}')
    import src.ai_interaction as ai
    monkeypatch.setattr(ai, 'get_session_manager', lambda: SimpleNamespace(get_session=lambda sid: None))
    settled = []
    monkeypatch.setattr(bg_monitor, '_settle_launch_effect', lambda job, record: settled.append(job))
    assert await bg_monitor._process_followup(rec) is bg_monitor.FollowupResult.TERMINAL_UNFOLLOWABLE
    assert settled == ([resource] if linkage == 'valid' else [])
    assert not monitor_session
