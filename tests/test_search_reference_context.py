import pytest
from src.clean_agent_preview import unbound_lookup_reference


@pytest.mark.parametrize('prompt', ['can u look it up', 'please find that', 'what about its price?', 'Could you search for this please?'])
def test_missing_subject_requires_context(prompt):
    history = [{'role': 'system', 'content': 'System'}, {'role': 'user', 'content': prompt}]
    assert unbound_lookup_reference(prompt, history)
    assert not unbound_lookup_reference(prompt, history, supplied_context=True)
    prior = [{'role': 'user', 'content': 'I am considering a Firefox phone.'}, {'role': 'assistant', 'content': 'Which features matter?'}]
    assert not unbound_lookup_reference(prompt, prior + history)


@pytest.mark.parametrize('prompt', ['Find the Python documentation', 'Look up this model: WH-1000XM5', 'What about its price compared to a Pixel 9?', 'Search for this https://example.org', 'What is it like living in Japan?', 'helo'])
def test_explicit_subjects_and_other_intents_keep_normal_routing(prompt):
    assert not unbound_lookup_reference(prompt, [{'role': 'user', 'content': prompt}])
