import pytest

from src.clean_agent_preview import inline_suggestion_request


@pytest.mark.parametrize('prompt', [
    'Suggest a short message asking to postpone that event by two hours. Do not modify my calendar.',
    'Suggest three names for a project.',
    'Give feedback on this idea: a shared shopping list.',
    'Review the pros and cons of postponing the meeting.',
])
def test_ordinary_advice_does_not_require_open_document(prompt):
    assert not inline_suggestion_request(prompt, require_editor_reference=True)


@pytest.mark.parametrize('prompt', [
    'Give suggestions to this document.',
    'Proofread the open document. Create inline suggestions only; do not apply changes.',
    'Provide inline comments.',
])
def test_explicit_editor_review_still_requires_document(prompt):
    assert inline_suggestion_request(prompt, require_editor_reference=True)


def test_visible_editor_can_still_accept_short_review_request():
    assert inline_suggestion_request('Proofread this.')
