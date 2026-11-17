"""Persist actual direct-child exit evidence, never inferred endpoint outcomes."""
import json
import time

import pytest

from tests.test_model_serving_control import controlled_runtime, plan_for


def test_actual_child_exit_is_recoverable_without_changing_task_state(controlled_runtime, tmp_path):
    from src.model_serving_status import read_local_services
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    state_before = manager.state_path.read_bytes()
    assert not (manager.log_dir / f'{sid}.exit.json').exists()
    children[0].terminate()  # This fixture's direct child handle only.
    actual_code = children[0].wait(timeout=5)
    file = manager.log_dir / f'{sid}.exit.json'
    deadline = time.monotonic() + 3
    while not file.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert file.is_file(), 'Actual direct-child exit evidence was not persisted'
    recovered = control.LocalServingController(manager.state_path, manager.log_dir)
    details = recovered.exit_details(sid)
    assert details is not None
    assert details['exit_code'] == actual_code
    assert details['observed_at'] > 0
    assert manager.state_path.read_bytes() == state_before, 'Exit observation cannot resurrect/cancel/finalize tasks'
    service = next(row for row in read_local_services(manager.state_path, manager.log_dir) if row['id'] == sid)
    assert service['exit'] == details
    assert recovered.control_permission(sid) is False


@pytest.mark.parametrize(('field', 'value'), [
    ('exit_code', True), ('exit_code', '0'),
    ('observed_at', float('nan')), ('observed_at', float('inf')),
    ('observed_at', True), ('observed_at', -1),
    ('start_token', 'unrelated-process'), ('source', 'browser'),
])
def test_invalid_exit_evidence_is_unknown(controlled_runtime, tmp_path, field, value):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    children[0].terminate()
    children[0].wait(timeout=5)
    file = manager.log_dir / f'{sid}.exit.json'
    deadline = time.monotonic() + 3
    while not file.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    event = json.loads(file.read_text())
    event[field] = value
    file.write_text(json.dumps(event), encoding='utf-8')
    assert manager.exit_details(sid) is None
    assert manager.control_permission(sid) is False


def test_exit_observer_failure_does_not_stop_a_valid_runtime(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    def unavailable(*args, **kwargs):
        raise RuntimeError('fixture thread resources exhausted')
    monkeypatch.setattr(control, 'Thread', unavailable)
    result = manager.start(plan_for(control, tmp_path))
    assert result['state'] == 'starting'
    assert any('Exit observation unavailable' in warning for warning in result['warnings'])
    assert children[0].poll() is None
    assert manager.control_permission(result['session_id']) is True
    assert manager.exit_details(result['session_id']) is None
