"""Controller tests use tiny Python HTTP fixtures, never inference runtimes."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_control():
    assert importlib.util.find_spec('src.model_serving_control'), 'typed serving controller is not implemented'
    from src import model_serving_control
    return model_serving_control


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def plan_for(control, tmp_path, **overrides):
    exe = tmp_path / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
    exe.write_bytes(b'fixture placeholder, never executed')
    model = tmp_path / 'fixture.gguf'
    model.write_bytes(b'GGUF' + b'\0' * 12)
    params = dict(engine='llamacpp', executable=str(exe), model_path=str(model), port=free_port(),
                  context_length=4096, model_context_limit=2048, kv_bytes_per_token=1024,
                  available_memory_bytes=8 * 1024**3)
    params.update(overrides)
    return control.build_launch_plan(**params)


def test_plan_limits_context_without_gpu_probe(tmp_path):
    control = load_control()
    plan = plan_for(control, tmp_path)
    assert plan['context_length'] == 2048
    assert plan['gpu_layers'] == 0
    assert '--ctx-size' in plan['argv']
    assert plan['memory_validation'] == 'estimated'
    assert 'nvidia-smi' not in ' '.join(plan['argv'])


def test_unknown_kv_estimate_requires_explicit_start_acknowledgement(tmp_path):
    control = load_control()
    plan = plan_for(control, tmp_path, kv_bytes_per_token=None)
    assert plan['memory_validation'] == 'unknown'
    manager = control.LocalServingController(tmp_path / 'state.json', tmp_path / 'logs')
    with pytest.raises(ValueError, match='memory'):
        manager.start(plan)


@pytest.mark.parametrize('engine', ['llama_cpp_python', 'python', 'unknown'])
def test_only_standalone_runtimes_are_launchable(tmp_path, engine):
    control = load_control()
    with pytest.raises(ValueError):
        plan_for(control, tmp_path, engine=engine)


@pytest.fixture
def controlled_runtime(tmp_path, monkeypatch):
    control = load_control()
    original = subprocess.Popen
    children = []
    def fixture_spawn(plan, log):
        # The only substitution is the executable: exercise real process spawn,
        # receipts, local HTTP and teardown without llama.cpp/Ollama or a model.
        from core.platform_compat import detached_popen_kwargs
        child = original([sys.executable, str(ROOT / 'tests/helpers/serving_runtime_fixture.py'),
                          '--port', str(plan['port'])], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **detached_popen_kwargs())
        children.append(child)
        return child
    monkeypatch.setattr(control, '_spawn_runtime', fixture_spawn)
    manager = control.LocalServingController(tmp_path / 'state.json', tmp_path / 'logs')
    yield control, manager, children
    for child in children:
        if child.poll() is None:
            child.terminate()  # Direct handle of this test's child only.
        child.wait(timeout=5)


def test_start_recover_and_stop_using_persisted_receipt(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    plan = plan_for(control, tmp_path)
    result = manager.start(plan)
    sid = result['session_id']
    assert result['state'] == 'starting'
    assert len(children) == 1
    # A fresh controller has no in-memory Popen handle, like an app restart.
    recovered = control.LocalServingController(manager.state_path, manager.log_dir)
    assert recovered.control_permission(sid) is True
    assert recovered.start(plan)['session_id'] == sid
    assert len(children) == 1, 'must not launch a duplicate after restart'
    stopped = recovered.stop(sid)
    assert stopped['process_stopped'] is True
    assert recovered.control_permission(sid) is False
    saved = json.loads(manager.state_path.read_text(encoding='utf-8'))
    assert saved['tasks'][0]['status'] == 'stopped'


def test_restart_revalidates_saved_plan_and_replaces_only_owned_child(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    started = manager.start(plan_for(control, tmp_path))
    recovered = control.LocalServingController(manager.state_path, manager.log_dir)
    result = recovered.restart(started['session_id'])
    assert result['ok'] is True
    assert result['state'] == 'starting', 'HTTP readiness is not established by spawn'
    assert result['previous_session_id'] == started['session_id']
    assert result['session_id'] != started['session_id']
    assert children[0].poll() is not None
    assert len(children) == 2
    assert recovered.control_permission(result['session_id']) is True


def test_external_listener_never_spawns_or_authorizes_stop(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    with socket.socket() as external:
        external.bind(('127.0.0.1', 0))
        external.listen()
        plan = plan_for(control, tmp_path, port=external.getsockname()[1])
        with pytest.raises(ValueError, match='port'):
            manager.start(plan)
    assert children == []
    assert manager.control_permission('external-ollama') is False
    with pytest.raises(ValueError):
        manager.stop('external-ollama')


@pytest.mark.parametrize('action', ['stop', 'restart'])
def test_recycled_receipt_refuses_stop_without_signalling(controlled_runtime, tmp_path, monkeypatch, action):
    control, manager, children = controlled_runtime
    result = manager.start(plan_for(control, tmp_path))
    sid = result['session_id']
    monkeypatch.setattr(control, 'verify', lambda pid, token: 'foreign')
    signalled = []
    monkeypatch.setattr(control, 'kill_process_tree', lambda *a, **kw: signalled.append(a))
    assert manager.control_permission(sid) is False
    assert manager.restart_permission(sid) is False
    with pytest.raises(ValueError, match='ownership'):
        getattr(manager, action)(sid)
    assert signalled == []
    assert children[0].poll() is None


@pytest.mark.parametrize('receipt_part', ['pid.token', 'serving.json'])
def test_missing_provenance_cannot_be_replaced_by_browser_state(controlled_runtime, tmp_path, receipt_part):
    control, manager, children = controlled_runtime
    result = manager.start(plan_for(control, tmp_path))
    sid = result['session_id']
    (manager.log_dir / f'{sid}.{receipt_part}').unlink()
    # The persisted task still says managed/running, and the process is alive;
    # neither fact replaces the missing server-owned launch receipt.
    assert manager.control_permission(sid) is False
    with pytest.raises(ValueError, match='ownership'):
        manager.stop(sid)
    assert children[0].poll() is None


def test_ollama_plan_only_starts_daemon_and_pins_its_own_port(tmp_path):
    control = load_control()
    exe = tmp_path / ('ollama.exe' if os.name == 'nt' else 'ollama')
    exe.write_bytes(b'not executed')
    plan = control.build_launch_plan(engine='ollama', executable=str(exe), port=11435)
    assert plan['argv'] == [str(exe.resolve()), 'serve']
    assert plan['env']['OLLAMA_HOST'] == '127.0.0.1:11435'
    assert plan['env']['OLLAMA_MAX_LOADED_MODELS'] == '1'
    assert plan['env']['OLLAMA_NOPRUNE'] == '1'
    assert plan['env']['OLLAMA_NO_CLOUD'] == '1'
    assert 'pull' not in plan['argv']


def test_live_launch_receipt_recovers_even_if_task_write_was_lost(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    result = manager.start(plan_for(control, tmp_path))
    manager.state_path.unlink()
    from src.model_serving_status import read_local_services
    services = read_local_services(manager.state_path, manager.log_dir)
    recovered = next(service for service in services if service['id'] == result['session_id'])
    assert recovered['source'] == 'receipt'
    assert recovered['management'] == 'odysseus-managed'
    assert recovered['process_ownership'] == 'owned'
    assert not manager.state_path.exists(), 'observation must not rewrite state'


def test_ram_budget_rejects_impossible_kv_cache(tmp_path):
    control = load_control()
    with pytest.raises(ValueError, match='memory'):
        plan_for(control, tmp_path, kv_bytes_per_token=1024**3)


def test_linux_memory_detection_without_gpu_commands(monkeypatch):
    from types import SimpleNamespace
    control = load_control()
    monkeypatch.setattr(control, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(control, 'Path', lambda value: SimpleNamespace(
        read_text=lambda **kwargs: 'MemTotal: 2000 kB\nMemAvailable: 1000 kB\n'))
    assert control.available_memory_bytes() == 1000 * 1024




def test_executable_kind_follows_server_os_not_client(tmp_path):
    control = load_control()
    foreign = tmp_path / ('llama-server' if os.name == 'nt' else 'llama-server.exe')
    foreign.write_bytes(b'wrong OS fixture')
    with pytest.raises(ValueError, match='executable'):
        plan_for(control, tmp_path, executable=str(foreign))


def test_restart_missing_model_preserves_running_child(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    plan = plan_for(control, tmp_path)
    sid = manager.start(plan)['session_id']
    Path(plan['model_path']).unlink()
    with pytest.raises(ValueError, match='GGUF'):
        manager.restart(sid)
    assert children[0].poll() is None
    assert len(children) == 1


def test_restart_unknown_fit_requires_ack_before_stop(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    monkeypatch.setattr(control, 'available_memory_bytes', lambda: None)
    with pytest.raises(ValueError, match='memory'):
        manager.restart(sid)
    assert children[0].poll() is None
    assert len(children) == 1


def test_explicit_restart_recovers_crashed_child_from_receipt(controlled_runtime, tmp_path):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    children[0].terminate()
    children[0].wait(timeout=5)
    recovered = control.LocalServingController(manager.state_path, manager.log_dir)
    assert recovered.control_permission(sid) is False
    assert recovered.restart_permission(sid) is True
    result = recovered.restart(sid)
    assert result['ok'] is True
    assert result['session_id'] != sid
    assert len(children) == 2


def test_restart_waits_for_confirmed_stop(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    monkeypatch.setattr(manager, 'stop', lambda _: {
        'ok': False, 'session_id': sid, 'state': 'stopping', 'process_stopped': False})
    result = manager.restart(sid)
    assert result['ok'] is False
    assert result['state'] == 'stopping'
    assert len(children) == 1
    assert children[0].poll() is None


def test_restart_reports_replacement_failure_without_false_success(controlled_runtime, tmp_path, monkeypatch):
    control, manager, children = controlled_runtime
    sid = manager.start(plan_for(control, tmp_path))['session_id']
    def busy_port(*args, **kwargs):
        raise ValueError('port is busy; external listener is not ours')
    monkeypatch.setattr(manager, 'start', busy_port)
    result = manager.restart(sid)
    assert result['ok'] is False
    assert result['state'] == 'restart-failed'
    assert result['process_stopped'] is True
    assert children[0].poll() is not None
    assert len(children) == 1
