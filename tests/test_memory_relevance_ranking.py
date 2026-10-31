"""Native memory ranking must not promote names on substring/first-person guesses."""
import pytest

from src.memory import MemoryManager


@pytest.fixture
def manager(tmp_path):
    return MemoryManager(str(tmp_path))


@pytest.mark.parametrize('query', ['bitcoin price', 'list tasks', 'time forecast', 'summary'])
def test_unrelated_query_does_not_force_name_into_results(manager, query):
    name = {'id':'name', 'text':'My name is Rowan Example'}
    match = {'id':'match', 'text':query}
    assert manager.get_relevant_memories(query, [name, match]) == [match]


@pytest.mark.parametrize('query, text', [
    ('what is my email', 'email is contact@example.test'),
    ('I like jazz', 'I like jazz music'),
    ('remind me about dentist', 'dentist appointment reminder'),
])
def test_first_person_words_do_not_override_the_actual_topic(manager, query, text):
    name = {'id':'name', 'text':'My name is Rowan Example'}
    topic = {'id':'topic', 'text':text}
    assert manager.get_relevant_memories(query, [name, topic], max_items=1) == [topic]


@pytest.mark.parametrize('query', ['Who am I?', 'what am I called', 'my name', 'NAME:'])
def test_explicit_identity_query_still_finds_name(manager, query):
    name = {'id':'name', 'text':'My name is Rowan Example'}
    assert manager.get_relevant_memories(query, [name]) == [name]


def test_closer_match_outranks_identity_signal_and_threshold_still_applies(manager):
    name = {'id':'name', 'text':'My name is Rowan Example'}
    project = {'id':'project', 'text':'the project name is Polaris'}
    assert manager.get_relevant_memories('what is the project name', [name, project], max_items=1) == [project]
    assert manager.get_relevant_memories('who am I', [name], threshold=0.7) == []


def test_name_is_still_searchable_by_its_actual_text_without_a_type_keyword(manager):
    name = {'id':'name', 'text':'My name is Rowan Example'}
    assert manager.get_relevant_memories('Rowan', [name]) == [name]


def test_empty_queries_and_limits_keep_their_contract(manager):
    memories = [{'text':'tea preference'}, {'text':'tea recipe'}]
    assert manager.get_relevant_memories('   ', memories) == []
    assert manager.get_relevant_memories('tea', []) == []
    assert len(manager.get_relevant_memories('tea', memories, max_items=1)) == 1
