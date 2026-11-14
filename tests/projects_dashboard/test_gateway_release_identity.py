"""The gateway ownership guard follows the gateway's own pid record across release activations, and the release sweep never removes a release a live gateway runs from."""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import service_controls as controls  # noqa: E402
from src import hermes_webapp_component as hw  # noqa: E402

OLD, NEW, THIRD = 'c49ae221f4f0', 'b1e8abf79b78', '2ae5b2b0a681'


def make(tmp_path, running_pid=7992):
    home = tmp_path / 'state' / 'hermes-gateway'
    home.mkdir(parents=True)
    manager = hw.WebappComponent.__new__(hw.WebappComponent)
    manager.directory = tmp_path / 'managed' / 'webapp'
    manager.last_actions = []
    for release in (OLD, NEW, THIRD):
        (manager.directory / 'releases' / release / 'src' / 'gateway').mkdir(parents=True)
        (manager.directory / 'releases' / release / 'src' / 'gateway' / 'run.py').write_text('x')
        (manager.directory / 'releases' / release / 'release.json').write_text(json.dumps({'state': 'ready'}))
    config = {'home': str(home), 'agent': str(manager.directory / 'releases' / NEW / 'src'), 'url': 'http://127.0.0.1:9130/', 'release': NEW, 'previous': THIRD}
    return manager, config, home


def record(home, pid=7992, entry=None, kind='hermes-gateway', hermes_home=None, manager=None, release=OLD):
    entry = entry or str(manager.directory / 'releases' / release / 'src' / 'gateway' / 'run.py')
    (home / 'gateway.pid').write_text(json.dumps({'pid': pid, 'kind': kind, 'argv': [entry], 'hermes_home': hermes_home or str(home)}))


def owned(manager, config, pid=7992):
    with mock.patch.object(controls, 'listening_pids', return_value=({pid}, True)):
        return manager.owned_listener(config)


def test_a_gateway_started_from_an_older_release_is_still_recognised_after_activation(tmp_path):
    manager, config, home = make(tmp_path)
    record(home, manager=manager, release=OLD)                       # the pointer says NEW, the running gateway was started from OLD
    assert owned(manager, config) == 7992


@pytest.mark.parametrize('bad', ['other_dir', 'wrong_file', 'traversal', 'bad_release_name', 'kind', 'home', 'pid'])
def test_the_guard_still_refuses_anything_that_is_not_this_components_gateway(tmp_path, bad):
    manager, config, home = make(tmp_path)
    releases = manager.directory / 'releases'
    kwargs = {'manager': manager}
    if bad == 'other_dir':
        kwargs['entry'] = str(tmp_path / 'elsewhere' / 'src' / 'gateway' / 'run.py')
    elif bad == 'wrong_file':
        kwargs['entry'] = str(releases / OLD / 'src' / 'gateway' / 'other.py')
    elif bad == 'traversal':
        kwargs['entry'] = str(releases / OLD / '..' / '..' / '..' / 'evil' / 'src' / 'gateway' / 'run.py')
    elif bad == 'bad_release_name':
        kwargs['entry'] = str(releases / 'not-a-release' / 'src' / 'gateway' / 'run.py')
    elif bad == 'kind':
        kwargs['kind'] = 'something-else'
    elif bad == 'home':
        kwargs['hermes_home'] = str(tmp_path / 'another-home')
    elif bad == 'pid':
        kwargs['pid'] = 1234
    record(home, **kwargs)
    with pytest.raises(controls.Unavailable, match='not the gateway Odysseus provisioned'):
        owned(manager, config)


def test_the_running_release_comes_from_the_live_pid_record_only(tmp_path):
    manager, config, home = make(tmp_path)
    record(home, manager=manager, release=OLD)
    with mock.patch.object(controls, 'processes', return_value={7992: (1, 'python.exe', '')}):
        assert manager.running_release(config) == OLD
    with mock.patch.object(controls, 'processes', return_value={}):      # the recorded pid is gone: nothing is protected by it
        assert manager.running_release(config) is None
    (home / 'gateway.pid').write_text('not json')
    assert manager.running_release(config) is None


def test_the_sweep_keeps_the_release_a_live_gateway_runs_from_with_its_bundle(tmp_path):
    manager, config, home = make(tmp_path)
    bundle = manager.directory / 'releases' / OLD / 'src' / 'apps' / 'desktop' / 'dist-webapp'
    bundle.mkdir(parents=True)
    (bundle / 'index.html').write_text('<html>')
    record(home, manager=manager, release=OLD)
    with mock.patch.object(controls, 'processes', return_value={7992: (1, 'python.exe', '')}):
        manager._sweep_releases(config)
    assert (bundle / 'index.html').is_file() and (manager.directory / 'releases' / NEW).is_dir() and (manager.directory / 'releases' / THIRD).is_dir()


def test_the_sweep_removes_an_unused_old_release_once_no_gateway_runs_from_it(tmp_path):
    manager, config, home = make(tmp_path)
    record(home, manager=manager, release=NEW)                              # gateway restarted onto the current release
    stale = manager.directory / 'releases' / OLD
    with mock.patch.object(controls, 'processes', return_value={7992: (1, 'python.exe', '')}):
        manager._sweep_releases(config)
    assert not stale.exists()


def test_a_release_file_in_use_does_not_abort_the_sweep(tmp_path):
    manager, config, home = make(tmp_path)
    record(home, manager=manager, release=NEW)
    with mock.patch.object(controls, 'processes', return_value={7992: (1, 'python.exe', '')}), \
            mock.patch.object(hw, '_remove_release_tree', side_effect=PermissionError('in use')):
        manager._sweep_releases(config)                                     # must not raise
    assert any('left in place' in action for action in manager.last_actions)
