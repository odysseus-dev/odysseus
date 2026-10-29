import pytest

from services.memory.memory_extractor import _has_user_fact_evidence, _is_text_duplicate


@pytest.mark.parametrize('content,evidence,expected', [
    ('I live in Lisbon.', 'I live in Lisbon', True),
    ('I live in Lisbon. Can you find cafes?', 'I live in Lisbon', True),
    ('Can you tell me where I live?', 'I live in Lisbon', False),
    ('I live in Lisbon?', 'I live in Lisbon', False),
    ('Please read my records.', 'My home is in Lisbon', False),
])
def test_extracted_memories_require_user_assertion(content, evidence, expected):
    assert _has_user_fact_evidence(
        {'text': 'User lives in Lisbon', 'evidence': evidence},
        [{'role': 'user', 'content': content}],
    ) is expected


def test_assistant_statement_does_not_ground_memory():
    assert not _has_user_fact_evidence(
        {'text': 'User lives in Lisbon', 'evidence': 'I live in Lisbon'},
        [{'role': 'assistant', 'content': 'I live in Lisbon'}],
    )


def test_name_dedup_accepts_refinement_but_preserves_different_slots():
    assert _is_text_duplicate("User's name is Alex.", [{'text': 'Name is Alex Morgan'}])
    assert not _is_text_duplicate("User's username is alex.", [{'text': "User's name is Alex."}])
