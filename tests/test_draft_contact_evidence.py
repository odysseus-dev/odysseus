import pytest

from src.clean_agent_preview import draft_contact_evidence_error


def check(args, observations=(), **kwargs):
    return draft_contact_evidence_error('mcp__email__draft_email', args,
        dependencies=('contacts',), executions=observations, **kwargs)


def contact(**overrides):
    return dict(tool='resolve_contact', execution_attempted=True, error=False,
        blocked=False, output='Jonathan Amos <jonathan@example.com>', **overrides)


def test_lookup_cannot_be_skipped():
    assert check({'to': 'jonathan@invented.example'})


def test_guessed_address_rejected_after_lookup():
    assert check({'to': 'jonathan@invented.example'}, [contact()])


def test_returned_address_and_explicit_cc_are_allowed():
    assert check({'to': 'Jonathan <JONATHAN@example.com>', 'cc': 'sam@example.com'},
                 [contact()], user_text='CC sam@example.com') is None


@pytest.mark.parametrize('field', ['error', 'blocked'])
def test_failed_lookup_does_not_ground_recipient(field):
    observation = contact()
    observation[field] = True
    assert check({'to': 'jonathan@example.com'}, [observation])


def test_unrelated_drafts_are_not_forced_to_lookup():
    assert draft_contact_evidence_error('draft_email', {'to': 'sam@example.com'}) is None
