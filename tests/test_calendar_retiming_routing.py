import pytest

from src.turn_contract import requested_capabilities
from src.clean_agent_preview import requests_mutation


@pytest.mark.parametrize('prompt', [
    'Push that event back by two hours.',
    'Please bring the meeting forward by 30 minutes.',
    'Could you postpone my appointment until Friday?',
    'Delay the event by one day.',
    'Shift the meeting to 16:00 UTC.',
])
def test_temporal_rescheduling_is_calendar_action(prompt):
    assert requested_capabilities(prompt) == {'calendar'}
    assert requests_mutation(prompt)


@pytest.mark.parametrize('prompt', [
    'Do not push that event back by two hours.',
    'Why did they postpone my appointment until Friday?',
    'Explain how to bring the meeting forward by 30 minutes.',
    'The event was delayed by one day.',
    'Push the code to the remote repository.',
])
def test_temporal_discussion_does_not_authorize_calendar_write(prompt):
    from src.turn_contract import calendar_retiming_request
    assert not calendar_retiming_request(prompt)
