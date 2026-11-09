"""Search errors and follow-ups over real disposable filesystem fixtures."""
import asyncio
import json
import os
import shutil

import pytest

from src.agent_tools.filesystem_tools import GrepTool


@pytest.mark.parametrize('fallback', [False, True])
def test_bound_scan_preserves_virtual_identity_and_omits_denied_files(tmp_path, monkeypatch, fallback):
    from src.agent_runtime.authority import ExactOperation
    from src.agent_runtime.resource_binding import bind_resource_operation, resolve_filesystem_operation
    from src.agent_runtime.resources import FilesystemRoot
    from src.tool_execution import _active_workspace
    (tmp_path / 'public.txt').write_text('BOUND_MATCH\n')
    secret = tmp_path / '.env'
    secret.write_text('BOUND_MATCH_SECRET\n')
    os.link(secret, tmp_path / 'alias.txt')
    (tmp_path / 'symlink.txt').symlink_to(secret)
    if fallback:
        real_which = shutil.which
        monkeypatch.setattr(shutil, 'which', lambda name, *a, **kw: None if name == 'rg' else real_which(name, *a, **kw))
    content = json.dumps({'path': '/workspace', 'pattern': 'BOUND_MATCH'})
    bound = resolve_filesystem_operation(ExactOperation.normalize('grep', content),
                                         roots=(FilesystemRoot.seal(tmp_path),), workspace=str(tmp_path))
    async def invoke():
        token = _active_workspace.set(str(tmp_path))
        try:
            with bind_resource_operation(bound):
                return await GrepTool().execute(bound.execution_input, {})
        finally:
            _active_workspace.reset(token)
    result = asyncio.run(invoke())
    assert result['exit_code'] == 0, result
    assert '/workspace/public.txt:1:BOUND_MATCH' in result['output']
    assert 'SECRET' not in result['output']
    assert 'alias.txt' not in result['output'] and 'symlink.txt' not in result['output']


def test_spawn_descriptor_refuses_replaced_inode_before_open(tmp_path, monkeypatch):
    import queue
    import builtins
    from src.agent_tools.filesystem_tools import _python_grep_worker
    path = tmp_path / 'target.txt'
    path.write_text('OLD')
    info, parent = path.stat(), tmp_path.stat()
    descriptor = (str(path), (info.st_dev, info.st_ino), ((str(tmp_path), (parent.st_dev, parent.st_ino)),))
    replacement = tmp_path / 'replacement'
    replacement.write_text('NEW_SECRET')
    os.replace(replacement, path)
    def forbidden(*args, **kwargs):
        raise AssertionError('stale descriptor opened a replacement')
    monkeypatch.setattr(builtins, 'open', forbidden)
    output = queue.Queue()
    _python_grep_worker({'pattern': 'SECRET', 'ignore_case': False, 'glob': '',
                         'base': str(tmp_path), 'max_hits': 2, 'files': (descriptor,)}, output)
    record = output.get_nowait()
    assert record[0] == 'error' and 'identity changed' in record[1]


def search(workspace, pattern, **args):
    from src.tool_execution import _active_workspace
    async def run():
        token = _active_workspace.set(str(workspace))
        try:
            return await GrepTool().execute(json.dumps({'pattern': pattern, **args}), {})
        finally:
            _active_workspace.reset(token)
    return asyncio.run(run())


def test_invalid_regex_is_error_not_empty_search(tmp_path):
    if not shutil.which('rg'):
        pytest.skip('Requires ripgrep backend')
    (tmp_path / 'sample.txt').write_text('alpha\nbeta\n', encoding='utf8')
    result = search(tmp_path, '[')
    assert result['exit_code'] == 1
    assert 'error' in result
    assert 'No matches' not in str(result)


def test_fallback_unreadable_file_is_error_not_empty(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip('Root bypasses filesystem read permission fixture')
    path = tmp_path / 'unreadable.txt'
    path.write_text('MATCH\n', encoding='utf8')
    path.chmod(0)
    real_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name, *args, **kwargs: None if name == 'rg' else real_which(name, *args, **kwargs))
    try:
        result = search(tmp_path, 'MATCH')
        assert result['exit_code'] == 1
        assert 'error' in result
    finally:
        path.chmod(0o600)
    assert 'No matches' not in str(result)


def test_single_file_search_retains_filename_and_line_for_followup(tmp_path):
    (tmp_path / 'sample.txt').write_text('alpha\nbeta\n', encoding='utf8')
    result = search(tmp_path, 'beta', path='/workspace/sample.txt')
    assert result['exit_code'] == 0
    assert '/workspace/sample.txt:2:beta' in result['output']
    assert str(tmp_path) not in result['output']


def test_fallback_does_not_follow_file_symlink_outside_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'inside.txt').write_text('MATCH_INSIDE\n', encoding='utf8')
    outside = tmp_path / 'outside.txt'
    outside.write_text('MATCH_OUTSIDE\n', encoding='utf8')
    (workspace / 'escape.txt').symlink_to(outside)
    real_which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name, *args, **kwargs: None if name == 'rg' else real_which(name, *args, **kwargs))
    result = search(workspace, 'MATCH')
    assert result['exit_code'] == 0
    assert 'MATCH_INSIDE' in result['output']
    assert 'MATCH_OUTSIDE' not in result['output']


@pytest.mark.parametrize('fallback', [False, True])
def test_missing_search_target_is_not_reported_empty(tmp_path, monkeypatch, fallback):
    if fallback:
        real_which = shutil.which
        monkeypatch.setattr(shutil, 'which', lambda name, *args, **kwargs: None if name == 'rg' else real_which(name, *args, **kwargs))
    result = search(tmp_path, 'alpha', path='/workspace/missing.txt')
    assert result['exit_code'] == 1
    assert 'error' in result


@pytest.mark.parametrize('fallback', [False, True])
def test_error_then_case_refinement_and_genuine_empty_result(tmp_path, monkeypatch, fallback):
    if fallback:
        real_which = shutil.which
        monkeypatch.setattr(shutil, 'which', lambda name, *args, **kwargs: None if name == 'rg' else real_which(name, *args, **kwargs))
    path = tmp_path / 'sample.txt'
    original = b'Alpha\nalpha\nviolet-72\n'
    path.write_bytes(original)
    assert search(tmp_path, '[')['exit_code'] == 1
    exact = search(tmp_path, 'alpha', path='/workspace/sample.txt')
    assert exact['exit_code'] == 0
    assert '/workspace/sample.txt:2:alpha' in exact['output']
    assert ':1:Alpha' not in exact['output']
    refined = search(tmp_path, 'alpha', path='/workspace/sample.txt', ignore_case=True)
    assert refined['exit_code'] == 0 and ':1:Alpha' in refined['output'] and ':2:alpha' in refined['output']
    empty = search(tmp_path, 'no-such-marker')
    assert empty['exit_code'] == 0 and 'No matches' in empty['output']
    assert path.read_bytes() == original
