"""Video routing must use the URL's actual host rather than a text substring."""
from types import SimpleNamespace

import pytest

from services.youtube import youtube_handler as youtube


@pytest.mark.parametrize('url', [
    'https://WWW.YOUTUBE.COM/watch?v=fixtureVideo',
    'https://YouTube.com/watch?v=fixtureVideo',
    'https://M.YouTube.Com/shorts/fixtureVideo',
    'https://MUSIC.YOUTUBE.COM/watch?v=fixtureVideo',
    'https://YOUTU.BE/fixtureVideo',
    'https://www.youtube.com/watch?v=fixtureVideo',
    'https://youtu.be/fixtureVideo',
])
def test_supported_hosts_are_case_insensitive_and_extractable(url):
    assert youtube.is_youtube_url(url)
    assert youtube.extract_youtube_id(url) == 'fixtureVideo'


@pytest.mark.parametrize('url', [
    'https://youtube.com.example.test/watch?v=fixtureVideo',
    'https://notyoutube.com/watch?v=fixtureVideo',
    'https://youtu.be.example.test/fixtureVideo',
    'https://example.test/path/youtube.com/watch',
    'https://example.test/?next=https://youtu.be/fixtureVideo',
    'https://youtube.com@example.test/watch?v=fixtureVideo',
    'https://[youtube.com/watch?v=fixtureVideo',
    'youtube.com is mentioned in this text',
    '',
])
def test_other_hosts_and_invalid_urls_are_not_youtube(url):
    assert youtube.is_youtube_url(url) is False


async def test_chat_preprocessing_reaches_transcript_for_uppercase_host(monkeypatch):
    from src import chat_handler
    calls = []

    async def transcript(url, video_id):
        calls.append(video_id)
        return {'success': True, 'transcript': 'Synthetic caption', 'video_id': video_id, 'segments': []}

    async def comments(video_id):
        return {'success': True, 'comments': []}

    monkeypatch.setattr(chat_handler, 'extract_transcript_async', transcript)
    monkeypatch.setattr(chat_handler, 'fetch_youtube_comments', comments)
    handler = chat_handler.ChatHandler(None, None, None, None, None, None)
    result = await handler.preprocess_message('Summarize https://WWW.YOUTUBE.COM/watch?v=fixtureVideo', [], SimpleNamespace(owner='alice'))
    assert calls == ['fixtureVideo']
    assert any('Synthetic caption' in text for text in result[3])
