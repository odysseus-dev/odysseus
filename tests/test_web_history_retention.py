from types import SimpleNamespace

from src.clean_agent_preview import conversation


def test_web_heavy_followup_keeps_conversation_subject():
    def answer(question, text):
        trace = [
            {'role': 'assistant', 'tool_calls': [{'id': 'search', 'function': {'name': 'web_search', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'search', 'content': 'Search result. ' * 2500},
            {'role': 'assistant', 'content': text},
        ]
        return [
            {'role': 'user', 'content': question},
            {'role': 'assistant', 'content': text, 'metadata': {'clean_v3_turn': trace}},
        ]

    session = SimpleNamespace(history=(
        answer('Look up news Ethan and Hila', 'The H3 podcast has paused.')
        + answer('Tell me more', 'The H3 podcast crew made a statement.')
        + [{'role': 'user', 'content': 'What did the crew members say?'}]
    ))
    result = conversation(session, [{'role': 'user', 'content': 'What did the crew members say?'}])
    assert [m['content'] for m in result if m['role'] == 'user'] == [
        'Look up news Ethan and Hila', 'Tell me more', 'What did the crew members say?'
    ]
    assert any('H3 podcast crew' in m.get('content', '') for m in result)
