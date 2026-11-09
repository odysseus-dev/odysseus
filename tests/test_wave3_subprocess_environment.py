"""Closed inheritance is the complete subprocess environment boundary."""
from src import tool_execution

def test_closed_subprocess_environment_drops_all_unlisted_credentials(monkeypatch):
    from unittest.mock import patch
    import os
    ambient = {'PATH': '/usr/bin', 'LANG': 'C.UTF-8', 'OPENAI_API_KEY': 'secret', 'HF_TOKEN': 'secret',
               'AUTH_ENABLED': 'false', 'DATABASE_URL': 'secret', 'PATH_TOKEN': 'secret',
               'AWS_SECRET_ACCESS_KEY': 'secret', 'ARBITRARY': 'secret', 'HOME': '/server/secret'}
    with patch.dict(os.environ, ambient, clear=True):
        child = tool_execution._agent_subprocess_env()
    assert child['PATH'] == '/usr/bin'
    assert child['HOME'] == tool_execution._AGENT_WORKDIR
    assert set(child) <= tool_execution._SAFE_SUBPROCESS_VARS | {'HOME', 'TERM', 'COLUMNS', 'LINES'}
    assert all(child.get(name) != value for name, value in ambient.items() if name not in {'PATH', 'LANG'})


async def test_real_python_child_does_not_inherit_ambient_credentials(workspace, monkeypatch):
    from tests.test_runtime_resource_integration import authority, dispatch
    for name in ('OPENAI_API_KEY', 'HF_TOKEN', 'PATH_TOKEN', 'DATABASE_URL', 'ODYSSEUS_INTERNAL_TOKEN'):
        monkeypatch.setenv(name, 'never-inherit-this-value')
    _, result = await dispatch(authority(workspace, 'python'), 'python',
        'import os\nprint(any(v == "never-inherit-this-value" for v in os.environ.values()))')
    assert result['exit_code'] == 0 and result['output'] == 'False'


from tests.test_runtime_resource_integration import workspace
