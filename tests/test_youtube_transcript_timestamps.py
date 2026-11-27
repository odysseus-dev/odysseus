"""Transcript timestamps must carry minutes into hours before reaching chat."""
from types import SimpleNamespace

import pytest

from services.youtube import youtube_handler as youtube


@pytest.mark.parametrize('start,expected', [
    (0, '00:00'), (9.9, '00:09'), (59.99, '00:59'), (60, '01:00'),
    (3599.99, '59:59'), (3600, '1:00:00'), (4530, '1:15:30'),
    (7199.9, '1:59:59'), (36001, '10:00:01'), (90061, '25:01:01'),
])
async def test_extracted_timestamp_and_chat_context(monkeypatch, start, expected):
    class TranscriptApi:
        def fetch(self, video_id):
            assert video_id == 'fixtureVideo'
            return [SimpleNamespace(start=start, duration=2.5, text=' Synthetic segment. ')]

    monkeypatch.setattr(youtube, 'YOUTUBE_AVAILABLE', True)
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', TranscriptApi)
    data = await youtube.extract_transcript_async('https://youtu.be/fixtureVideo', 'fixtureVideo')
    assert data['success']
    assert data['segments'] == [{
        'text': 'Synthetic segment.', 'start': start, 'duration': 2.5, 'timestamp': expected,
    }]
    assert data['transcript'] == 'Synthetic segment.'
    context = youtube.format_transcript_for_context(data, 'https://youtu.be/fixtureVideo')
    assert f'[{expected}] Synthetic segment.' in context


async def test_mixed_long_transcript_keeps_blank_filtering_and_segment_order(monkeypatch):
    class TranscriptApi:
        def fetch(self, video_id):
            return [SimpleNamespace(start=start, duration=1, text=text) for start, text in [
                (0, 'Intro'), (3600, '  '), (4530.5, 'Later'), (7200, 'Ending'),
            ]]

    monkeypatch.setattr(youtube, 'YOUTUBE_AVAILABLE', True)
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', TranscriptApi)
    data = await youtube.extract_transcript_async('https://youtu.be/fixtureVideo', 'fixtureVideo')
    assert data['success']
    assert [(s['timestamp'], s['text']) for s in data['segments']] == [
        ('00:00', 'Intro'), ('1:15:30', 'Later'), ('2:00:00', 'Ending'),
    ]
    assert data['transcript'] == 'Intro Later Ending'
