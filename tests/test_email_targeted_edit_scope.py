from types import SimpleNamespace

import pytest

from src.clean_agent_preview import active_editor_whole_draft_request


@pytest.mark.parametrize('prompt', [
    'Change Tuesday to Wednesday only in my reply, not in the quoted email.',
    'Fix the spelling in this draft.',
    'Remove the last sentence from my reply.',
    'Replace "draft" with "final" in the subject.',
    'Do not write another reply. Correct the date only.',
])
def test_targeted_email_edit_does_not_force_whole_body_replacement(prompt):
    document = SimpleNamespace(title='Fixture', language='email', current_content='Existing reply')
    assert not active_editor_whole_draft_request(document, prompt)


@pytest.mark.parametrize('prompt', [
    'Write reply this email', 'Draft a reply', 'Please respond to this email.',
    'Could you write a reply saying I can attend?', 'Reply politely declining.',
])
def test_composition_command_still_selects_whole_draft_writer(prompt):
    document = SimpleNamespace(title='Fixture', language='email', current_content='Existing reply')
    assert active_editor_whole_draft_request(document, prompt)
