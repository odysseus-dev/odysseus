"""Effective RAG policy reaches the real search and context consumer."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.chat_processor import ChatProcessor
from tests.test_kv_cache_invalidation_2927 import _build_context_harness

pytestmark = pytest.mark.area_routes


@pytest.mark.parametrize('mode', ['chat', 'agent', 'compare'])
@pytest.mark.parametrize('policy', [
    'normal', 'incognito', 'preprocessing_disabled', 'research_spinoff', 'low_signal',
])
@pytest.mark.parametrize('use_rag', [None, 'true', 'false', True, False])
async def test_rag_policy_preserves_context_channels(monkeypatch, mode, policy, use_rag):
    """Exercise effective policy with real preface assembly and synthetic I/O.

    Nonempty results prove permitted retrieval still injects context. Independent
    memory, skills and web spies ensure a RAG disable cannot disable other channels.
    """
    from routes import chat_helpers
    from src import chat_processor, llm_core, user_time

    sess, request, handler, _ = _build_context_harness(monkeypatch, chat_helpers, history=[])
    monkeypatch.setattr(chat_helpers, 'load_prefs_for_user', lambda user: {
        'memory_enabled': True, 'skills_enabled': True})
    monkeypatch.setattr(user_time, 'current_datetime_context_message', lambda: {
        'role': 'user', 'content': 'Synthetic current date context'})
    incognito = policy == 'incognito'
    preprocessing = policy != 'preprocessing_disabled'
    if policy == 'research_spinoff':
        sess.history = [{'role': 'system', 'metadata': {'research_spinoff_from': 'fixture-report'}}]
    message = 'hi' if policy == 'low_signal' else 'Find the retention policy for archived project documents'
    search = Mock(return_value=[{
        'document': 'Synthetic retention document',
        'metadata': {'filename': 'fixture.txt'}, 'similarity': 0.99,
    }])
    memory = Mock(return_value=[{
        'id': 'fixture-memory', 'text': 'User identity synthetic memory',
        'category': 'identity', 'pinned': True,
    }])
    skills = Mock(return_value=[{'name': 'fixture_skill', 'description': 'Synthetic skill'}])
    web = Mock(return_value=('Synthetic web context', [{'url': 'https://example.invalid/fixture'}]))
    query = Mock(return_value='retention policy')
    monkeypatch.setattr(llm_core, 'llm_call', query)
    monkeypatch.setattr(chat_processor, 'comprehensive_web_search', web)
    processor = ChatProcessor(
        memory_manager=SimpleNamespace(load=memory),
        personal_docs_manager=SimpleNamespace(rag_manager=SimpleNamespace(search=search)),
        skills_manager=SimpleNamespace(index_for=skills),
    )
    received = []
    real_preface = processor.build_context_preface

    def capture(**kwargs):
        received.append(dict(kwargs))
        return real_preface(**kwargs)

    monkeypatch.setattr(processor, 'build_context_preface', capture)
    ctx = await chat_helpers.build_chat_context(
        sess=sess, request=request, chat_handler=handler, chat_processor=processor,
        message=message, session_id='c5-policy', use_rag=use_rag,
        incognito=incognito, allow_tool_preprocessing=preprocessing,
        persist_user_message=False, agent_mode=mode == 'agent',
        compare_mode=mode == 'compare', use_web='true',
        search_context='Synthetic prefetched context' if mode == 'compare' else None,
    )
    rag_allowed = policy == 'normal' and str(use_rag).lower() != 'false'
    assert len(received) == 1
    assert search.call_count == int(rag_allowed)
    assert received[0]['use_rag'] is rag_allowed
    if rag_allowed:
        search.assert_called_once_with(message, k=5, owner='tester')
        assert ctx.rag_sources == [{
            'filename': 'fixture.txt', 'snippet': 'Synthetic retention document', 'similarity': 0.99,
        }]
    else:
        assert ctx.rag_sources == []
    text = '\n'.join(m.get('content', '') for m in ctx.messages)
    assert ('Synthetic retention document' in text) is rag_allowed

    memory_allowed = policy == 'normal'
    assert received[0]['use_memory'] is memory_allowed
    assert memory.call_count == int(memory_allowed)
    assert ('User identity synthetic memory' in text) is memory_allowed
    assert bool(ctx.used_memories) is memory_allowed
    skills_allowed = policy in {'normal', 'research_spinoff'}
    assert received[0]['use_skills'] is skills_allowed
    assert skills.call_count == int(skills_allowed and mode == 'agent')
    assert ('fixture_skill' in text) is (skills_allowed and mode == 'agent')
    web_allowed = preprocessing and policy != 'low_signal' and mode != 'compare'
    assert received[0]['use_web'] is web_allowed
    assert web.call_count == query.call_count == int(web_allowed)
    assert ('Synthetic web context' in text) is web_allowed
    assert bool(ctx.web_sources) is web_allowed
    assert ('Synthetic prefetched context' in text) is (
        mode == 'compare' and preprocessing and policy != 'low_signal')
