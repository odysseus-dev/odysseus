"""The Email reader's Spam action only removes confirmed successful moves."""
import json
from pathlib import Path
import subprocess

import pytest

from tests.helpers.js_modules import js_function_source


RUNNER = Path(__file__).parent / 'helpers' / 'email_move_menu_runner.cjs'


def run(mode):
    source = '\n'.join(js_function_source(name) for name in [
        '_showReaderMoreMenu', '_requireSuccessfulEmailMutation',
    ])
    result = subprocess.run(['node', str(RUNNER)],
                            input=json.dumps({'source': source, 'mode': mode}),
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('mode', [
    'http-error', 'success-false', 'invalid-json', 'missing-success', 'network-error', 'pending',
])
def test_rejected_spam_move_keeps_message_and_cache_and_shows_error(mode):
    result = run(mode)

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


def test_confirmed_spam_move_removes_message_and_updates_cache():
    result = run('success')

    assert result['uids'] == []
    assert result['calls']['animations'] == [['42']]
    assert result['calls']['cache_writes'] == 1
    assert result['calls']['renders'] == 1
    assert result['calls']['errors'] == []
    assert result['unhandled'] == []
    request, = result['calls']['requests']
    assert request['method'] == 'POST'
    assert 'account_id=owned-account' in request['url']
    assert '/move/42?' in request['url']
    assert 'folder=INBOX' in request['url']
    assert 'dest=Junk' in request['url']
