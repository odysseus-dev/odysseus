"""Temporary-path remapping must preserve workspace and interpolated paths."""
import json

import pytest

from src import tool_execution
from src.agent_tools import subprocess_tools as tools
from tests.process_resource_helpers import authorized_handler


@pytest.mark.parametrize('tool', ['bash', 'python'])
@pytest.mark.parametrize('path', [
    './tmp/a', '~/tmp/a', '/var/tmp/a', '$HOME/tmp/a', '${HOME}/tmp/a',
    'data/tmp/a', '{root}/tmp/a', '/srv/ws/tmp/a', 'https://example.test/tmp/a',
])
async def test_longer_paths_are_not_rewritten_or_create_scratch(tool, path, tmp_path, monkeypatch):
    monkeypatch.setattr(tool_execution, 'agent_cwd', lambda: str(tmp_path))
    captured = []

    async def run(command, ctx, **kwargs):
        captured.append(command[-1] if kwargs.get('argv') else command)
        return {'exit_code': 0}

    monkeypatch.setattr(tools, '_run_owned_command', run)
    source = f'cat {path}' if tool == 'bash' else f'print({json.dumps(path)})'
    cls = tools.BashTool if tool == 'bash' else tools.PythonTool
    await authorized_handler(cls().execute, tmp_path)(source, {})
    assert path in captured[0]
    assert not (tmp_path / tools.AGENT_ISOLATED_TMP_DIRNAME).exists()


@pytest.mark.parametrize('tool', ['bash', 'python'])
@pytest.mark.parametrize('source', [
    '/tmp/a', 'cat /tmp/a', "cat '/tmp/a'", 'x="/tmp/a"',
    'open("/tmp/a")', "paths=['/tmp/a','/tmp/b']", 'x:/tmp/a',
])
async def test_absolute_tmp_tokens_still_use_workspace_scratch(tool, source, tmp_path, monkeypatch):
    monkeypatch.setattr(tool_execution, 'agent_cwd', lambda: str(tmp_path))
    captured = []

    async def run(command, ctx, **kwargs):
        captured.append(command[-1] if kwargs.get('argv') else command)
        return {'exit_code': 0}

    monkeypatch.setattr(tools, '_run_owned_command', run)
    cls = tools.BashTool if tool == 'bash' else tools.PythonTool
    await authorized_handler(cls().execute, tmp_path)(source, {})
    scratch = tmp_path / tools.AGENT_ISOLATED_TMP_DIRNAME
    assert str(scratch) + '/a' in captured[0]
    assert scratch.is_dir()


@pytest.mark.parametrize('tool', ['bash', 'python'])
async def test_mixed_paths_and_replacement_text_are_not_recursively_rewritten(tool, tmp_path, monkeypatch):
    workspace = tmp_path / 'tmp' / 'workspace'
    workspace.mkdir(parents=True)
    monkeypatch.setattr(tool_execution, 'agent_cwd', lambda: str(workspace))
    captured = []

    async def run(command, ctx, **kwargs):
        captured.append(command[-1] if kwargs.get('argv') else command)
        return {'exit_code': 0}

    monkeypatch.setattr(tools, '_run_owned_command', run)
    cls = tools.BashTool if tool == 'bash' else tools.PythonTool
    source = "print('/tmp/a', './tmp/b', '/var/tmp/c', '/tmp/d')"
    await authorized_handler(cls().execute, workspace)(source, {})
    expected = source.replace("'/tmp/", "'" + str(workspace / tools.AGENT_ISOLATED_TMP_DIRNAME) + '/')
    assert expected in captured[0]


@pytest.mark.parametrize('tool', ['bash', 'python'])
async def test_unadmitted_command_cannot_create_scratch_or_run(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool_execution, 'agent_cwd', lambda: str(tmp_path))

    async def forbidden(*args, **kwargs):
        pytest.fail('denied producer reached command execution')

    monkeypatch.setattr(tools, '_run_owned_command', forbidden)
    cls = tools.BashTool if tool == 'bash' else tools.PythonTool
    source = 'cat /tmp/a' if tool == 'bash' else 'print(open("/tmp/a").read())'
    result = await cls().execute(source, {})
    assert result['blocked'] is True
    assert result['failure_kind'] == 'resource_identity_denied'
    assert not (tmp_path / tools.AGENT_ISOLATED_TMP_DIRNAME).exists()
