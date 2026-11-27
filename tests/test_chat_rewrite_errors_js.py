"""Provider SSE failures must remain failures in the public rewrite controller."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def run_rewrite(stream='', **options):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    result = subprocess.run(
        ['node', 'tests/helpers/chat_rewrite_errors.mjs', json.dumps({'stream': stream, **options})],
        cwd=_REPO, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('event,payload,expected', [
    ('error', {'status': 401, 'text': 'Credentials rejected; reconnect.'}, 'Credentials rejected; reconnect.'),
    ('error', {'status': 503, 'text': 'Provider unavailable'}, 'Provider unavailable'),
    ('error', {'status': 403, 'error': {'message': 'Access denied'}}, 'Access denied'),
    ('error', {'status': 429}, 'Error 429'),
    ('error', {}, 'Error unknown'),
    ('', {'status': 401, 'text': 'Invalid key'}, 'Invalid key'),
    ('', {'error': 'Existing error'}, 'Existing error'),
])
@pytest.mark.parametrize('chunk_size', [1, 4096])
def test_provider_errors_restore_original_and_never_save_a_variant(event, payload, expected, chunk_size):
    stream = 'data: {"delta":"Partial rewrite"}\n\n'
    stream += (f'event: {event}\n' if event else '') + 'data: ' + json.dumps(payload) + '\n\n'
    result = run_rewrite(stream, chunk_size=chunk_size)
    assert result['errors'] == ['Rewrite failed: ' + expected]
    assert result['raw'] == 'Original answer'
    assert result['html'] == '<p>Original answer</p>'
    assert result['variants'] is None
    assert [r['url'] for r in result['requests']] == ['/api/rewrite']
    assert result['destroyed'] == 1
    assert result['navigation'] == 0


def test_normal_events_and_reasoning_do_not_hide_successful_rewrite():
    result = run_rewrite('event: progress\ndata: {"status":200,"text":"Working"}\n\n'
                         'data: {"thinking":true,"delta":"Private reasoning"}\n\n'
                         'data: {"delta":"Short answer"}\n\ndata: [DONE]\n\n', chunk_size=3)
    assert result['errors'] == []
    assert result['raw'] == 'Short answer'
    assert [v['raw'] for v in json.loads(result['variants'])] == ['Original answer', 'Short answer']
    assert len(result['requests']) == 2
    assert result['navigation'] == 1
    assert result['destroyed'] == 1


@pytest.mark.parametrize('options,expected', [
    ({}, 'model returned no rewritten text'),
    ({'http_status': 502}, 'HTTP 502'),
])
def test_empty_and_http_failures_keep_existing_error_behavior(options, expected):
    result = run_rewrite(**options)
    assert result['errors'] == ['Rewrite failed: ' + expected]
    assert result['raw'] == 'Original answer'
    assert result['destroyed'] == 1
