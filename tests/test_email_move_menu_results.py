"""Email move menus only remove messages after confirmed provider success."""
import json
from pathlib import Path
import subprocess

import pytest

from tests.helpers.js_modules import js_function_source


RUNNER = Path(__file__).parent / 'helpers' / 'email_move_menu_runner.cjs'
FLOWS = ['card-inbox', 'card-sent', 'reader-archive', 'reader-spam']


def run(flow, mode):
    source = '\n'.join(js_function_source(name) for name in [
        '_showReaderMoreMenu', '_showCardMenu', '_requireSuccessfulEmailMutation',
    ])
    result = subprocess.run(['node', str(RUNNER)],
                            input=json.dumps({'source': source, 'flow': flow, 'mode': mode}),
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('flow', FLOWS)
@pytest.mark.parametrize('mode', [
    'http-error', 'success-false', 'invalid-json', 'missing-success', 'network-error', 'pending',
])
def test_rejected_move_keeps_message_and_cache_and_shows_error(flow, mode):
    result = run(flow, mode)

    if mode == 'pending':
        assert result['while_pending'] == {
            'uids': ['42'], 'animations': 0, 'cache_writes': 0, 'error_count': 0,
        }
    assert result['uids'] == ['42']
    assert result['calls']['animations'] == []
    assert result['calls']['cache_writes'] == 0
    assert result['calls']['renders'] == 0
    assert len(result['calls']['errors']) == 1
    assert result['unhandled'] == []


@pytest.mark.parametrize('flow', FLOWS)
def test_confirmed_move_removes_message_and_updates_cache(flow):
    result = run(flow, 'success')

    assert result['uids'] == []
    assert result['calls']['animations'] == [['42']]
    assert result['calls']['cache_writes'] == 1
    assert result['calls']['renders'] == 1
    assert result['calls']['errors'] == []
    assert result['unhandled'] == []
    request, = result['calls']['requests']
    assert request['method'] == 'POST'
    assert 'account_id=owned-account' in request['url']
    assert ('folder=Sent' in request['url']) == (flow == 'card-sent')
