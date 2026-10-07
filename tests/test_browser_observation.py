import json

from src.clean_agent_preview import preview_tool_result_text


def test_legacy_page_text_retains_access_block_evidence():
    result = {'url': 'https://example.org', 'title': 'Security verification',
              'text': 'Unusual traffic. Complete the CAPTCHA.'}
    output = preview_tool_result_text({'output': json.dumps(result), 'exit_code': 0},
                                     'private_browser', {})
    assert 'Security verification' in output
    assert 'Complete the CAPTCHA' in output


def test_snapshot_survives_large_duplicate_refs():
    result = {'refs': {f'e{i}': {'name': 'noise' * 50} for i in range(1000)},
              'origin': 'https://example.org',
              'snapshot': '- button "Categories" [ref=e12]\n- link "Co-Operative" [ref=e999]'}
    output = preview_tool_result_text({'output': json.dumps([{'result': result}]), 'exit_code': 0},
                                     'private_browser', {})
    assert 'Co-Operative' in output and '[ref=e999]' in output
    assert 'Categories' in output and 'https://example.org' in output
    assert 'noise' not in output and len(output) < 300


def test_failed_click_retains_error_and_updated_refs():
    output = preview_tool_result_text({'exit_code': 1, 'output':
        'Element covered\n\n[page state after failed click]\n' + json.dumps([
            {'result': {'snapshot': '- dialog "Choices"\n- button "Close" [ref=e2]'}}])},
        'private_browser', {'action': 'click'})
    assert 'Exit code: 1' in output and 'Element covered' in output
    assert 'Close' in output and '[ref=e2]' in output


def test_long_snapshot_is_bounded_with_explicit_omission():
    snapshot = '\n'.join(f'- link "Item {i}" [ref=e{i}]' for i in range(2000))
    output = preview_tool_result_text({'output': json.dumps({'snapshot': snapshot})}, 'private_browser', {})
    assert len(output) <= 8000
    assert 'shortened at line boundaries' in output
    assert '[ref=e0]' in output and '[ref=e1999]' in output
