import json

from src.clean_agent_preview import preview_tool_result_text


def test_successful_edit_exposes_saved_source_not_old_find():
    observation = json.loads(preview_tool_result_text(
        {'doc_id': 'fixture', 'applied': 1, 'version': 2, 'content': 'new wording'},
        'edit_document', {'edits': [{'find': 'old wording', 'replace': 'new wording'}]},
    ))
    assert observation['current_content'] == 'new wording'
    assert observation['version'] == 2
    assert 'Saved source' in observation['content_state']


def test_large_edit_observation_keeps_partial_failure_details():
    observation = json.loads(preview_tool_result_text(
        {'doc_id': 'fixture', 'applied': 1, 'content': 'a' * 10000,
         'partial': True, 'rejected': 1, 'invalid_edits': [{'number': 2}]},
        'edit_document', {},
    ))
    assert 'current_content' not in observation
    assert observation['invalid_edits'] == [{'number': 2}]
    assert 'Retry only' in observation['instruction']
