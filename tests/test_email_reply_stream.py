import json
import pytest
from src.email_reply_stream import reply_body, stream_reply


def test_analysis_is_not_a_reply():
    assert reply_body('The user wants me to draft a reply. Context Analysis: ...', complete=True) == ''
    assert reply_body('<think>analysis</think>', complete=True) == ''
    assert reply_body('<<<REPLY>>>Hi Jonathan', complete=True) == ''
    assert reply_body('<<<REPLY>>><think>analysis</think>Hi<<<END>>>', complete=True) == ''
    assert reply_body('analysis\n<<<REPLY>>>Hi Jonathan,\nThanks.<<<END>>>', complete=True) == 'Hi Jonathan,\nThanks.'


@pytest.mark.asyncio
async def test_stream_filters_reasoning_and_split_markers(monkeypatch):
    from src import llm_core
    calls, events = [], []
    async def model(*args, **kwargs):
        calls.append(kwargs)
        for delta in ['Private analysis.', '<<<REP', 'LY>>>Hi Jonathan,', '\nThanks.', '<<<EN', 'D>>>']:
            yield 'data: ' + json.dumps({'delta': delta}) + '\n\n'
    async def emit(event): events.append(event)
    monkeypatch.setattr(llm_core, 'stream_llm', model)
    raw, used = await stream_reply([('http://local', 'Ajax', {})], [], emit)
    assert used == 'Ajax'
    assert calls[0]['thinking_mode'] == 'off'
    assert events[-1]['text'] == 'Hi Jonathan,\nThanks.'
    assert all('analysis' not in e['text'] and '<<<' not in e['text'] for e in events)


@pytest.mark.asyncio
async def test_incomplete_stream_clears_partial_draft(monkeypatch):
    from src import llm_core
    events = []
    async def model(*args, **kwargs):
        yield 'data: ' + json.dumps({'delta': '<<<REPLY>>>Partial'}) + '\n\n'
    async def emit(event): events.append(event)
    monkeypatch.setattr(llm_core, 'stream_llm', model)
    with pytest.raises(ValueError, match='incomplete'):
        await stream_reply([('http://local', 'Ajax', {})], [], emit)
    assert events[-1]['text'] == ''
