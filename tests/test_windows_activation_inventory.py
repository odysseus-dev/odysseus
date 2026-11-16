"""Identity-aware reconciliation observes receipts without deleting history."""
from pathlib import Path
import pytest

from tests.test_windows_app_restart import files


def test_foreign_receipt_is_obsolete_not_a_worker_or_stop_target(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    pid = logs / 'cookbook-old.pid'
    token = logs / 'cookbook-old.pid.token'
    pid.write_text('321')
    token.write_text('win32:100')
    monkeypatch.setattr(control.win, 'identity', lambda number: {'pid': number, 'start_token': 'win32:200'})
    before = {path: path.read_bytes() for path in (pid, token, state / 'cookbook_state.json')}
    result = control.file_guard(state, logs, db)
    assert result['clear'] is True, 'A fresh foreign identity is not the recorded worker'
    assert result['worker_receipts'][0]['state'] == 'obsolete'
    assert result['worker_receipts'][0]['signal_authorized'] is False
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize('token', [None, 'win32:200', 'win32:000100', 'broken'])
def test_live_matching_or_unknown_receipt_still_blocks(tmp_path, monkeypatch, token):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (logs / 'cookbook-old.pid').write_text('321')
    if token is not None:
        (logs / 'cookbook-old.pid.token').write_text(token)
    monkeypatch.setattr(control.win, 'identity', lambda number: {'pid': number, 'start_token': 'win32:200'})
    assert control.file_guard(state, logs, db)['clear'] is False


def test_replaced_receipt_during_observation_is_unknown(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (logs / 'cookbook-old.pid').write_text('321')
    token = logs / 'cookbook-old.pid.token'
    token.write_text('win32:100')
    def identity(pid):
        token.write_text('win32:200')
        return {'pid': pid, 'start_token': 'win32:200'}
    monkeypatch.setattr(control.win, 'identity', identity)
    assert control.file_guard(state, logs, db)['clear'] is False


def test_missing_registry_requires_independent_uninitialized_evidence(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (state / 'bg_jobs.json').unlink()  # Fixture only: represent an installation before its first job.
    publication = state / 'process_resources'
    publication.mkdir()
    (publication / 'publication.lock').write_text('')
    monkeypatch.setattr(control, '_native_background_processes',
                        lambda: {'complete': True, 'supervisors': []}, raising=False)
    result = control.file_guard(state, logs, db)
    assert result['clear'] is True, 'Missing alone is insufficient, but independently proven pre-launch state is idle'
    assert result['background_jobs']['state'] == 'uninitialized_idle'
    assert not (state / 'bg_jobs.json').exists(), 'Never manufacture an empty job registry'


def test_unreadable_launch_inventory_is_unknown_not_an_exception_or_idle(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (state / 'bg_jobs.json').unlink()
    publication = state / 'process_resources'
    publication.mkdir()
    original = Path.iterdir
    def denied(path):
        if path == publication:
            raise PermissionError('fixture denied')
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', denied)
    assert control.file_guard(state, logs, db)['clear'] is False


@pytest.mark.parametrize('evidence', ['job-directory', 'publication', 'containment', 'incomplete-scan', 'supervisor'])
def test_missing_registry_with_partial_or_unknown_evidence_blocks(tmp_path, monkeypatch, evidence):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (state / 'bg_jobs.json').unlink()
    if evidence == 'job-directory':
        (state / 'bg_jobs').mkdir()
    elif evidence == 'publication':
        (state / 'process_resources').mkdir()
        (state / 'process_resources/launch.json').write_text('{}')
    elif evidence == 'containment':
        (state / 'containment_grants.json').write_text('{}')
    monkeypatch.setattr(control, '_native_background_processes', lambda: {
        'complete': evidence != 'incomplete-scan',
        'supervisors': [321] if evidence == 'supervisor' else []})
    assert control.file_guard(state, logs, db)['clear'] is False


def test_launch_artifacts_appearing_during_scan_block(tmp_path, monkeypatch):
    from src import windows_app_restart as control
    state, logs, db = files(tmp_path)
    (state / 'bg_jobs.json').unlink()
    def scan():
        (state / 'bg_jobs').mkdir()
        return {'complete': True, 'supervisors': []}
    monkeypatch.setattr(control, '_native_background_processes', scan)
    assert control.file_guard(state, logs, db)['clear'] is False
