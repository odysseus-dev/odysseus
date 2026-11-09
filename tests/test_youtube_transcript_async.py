"""Synchronous transcript SDK work must not monopolize the asyncio loop."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from services.youtube import youtube_handler as youtube


def _snippet(text='Synthetic transcript'):
    return SimpleNamespace(start=1, duration=2, text=text)


def _enable(monkeypatch, api):
    monkeypatch.setattr(youtube, 'YOUTUBE_AVAILABLE', True)
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', api)


async def test_sdk_construction_fetch_and_iteration_run_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen = []

    class Transcript:
        def __iter__(self):
            seen.append(('iteration', threading.get_ident()))
            yield _snippet()

    class Api:
        def __init__(self):
            seen.append(('construction', threading.get_ident()))

        def fetch(self, video_id):
            assert video_id == 'fixture'
            seen.append(('fetch', threading.get_ident()))
            return Transcript()

    _enable(monkeypatch, Api)
    result = await youtube.extract_transcript_async('https://youtu.be/fixture', 'fixture')
    assert result['success'] and result['transcript'] == 'Synthetic transcript'
    assert [stage for stage, _ in seen] == ['construction', 'fetch', 'iteration']
    assert all(identity != loop_thread for _, identity in seen)
    assert len({identity for _, identity in seen}) == 1


async def test_event_loop_progresses_while_sdk_is_waiting(monkeypatch):
    started, release = threading.Event(), threading.Event()
    order = []

    class Api:
        def fetch(self, video_id):
            started.set()
            release.wait(timeout=2)
            order.append('fetch-finished')
            return [_snippet()]

    async def heartbeat():
        while not started.is_set():
            await asyncio.sleep(.001)
        order.append('heartbeat')
        release.set()

    _enable(monkeypatch, Api)
    try:
        result, _ = await asyncio.gather(
            youtube.extract_transcript_async('https://youtu.be/fixture', 'fixture'),
            heartbeat(),
        )
        assert result['success']
        assert order.index('heartbeat') < order.index('fetch-finished')
    finally:
        release.set()


async def test_transient_sdk_failure_retries_with_the_existing_backoff(monkeypatch):
    calls = []

    class Api:
        def fetch(self, video_id):
            calls.append(video_id)
            if len(calls) == 1:
                raise RuntimeError('Synthetic transient SDK failure')
            return [_snippet()]

    _enable(monkeypatch, Api)
    result = await youtube.extract_transcript_async('https://youtu.be/fixture', 'fixture', max_retries=2)
    assert result['success'] and calls == ['fixture', 'fixture']
    assert result['segments'][0]['text'] == 'Synthetic transcript'


async def test_cancelled_await_does_not_retry_the_inflight_sdk_call(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    class Api:
        def fetch(self, video_id):
            calls.append(video_id)
            started.set()
            release.wait(timeout=2)
            return [_snippet()]

    _enable(monkeypatch, Api)
    task = asyncio.create_task(youtube.extract_transcript_async('https://youtu.be/fixture', 'fixture'))
    try:
        while not started.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ['fixture']
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_concurrent_transcripts_use_independent_sdk_instances(monkeypatch):
    meeting = threading.Barrier(2, timeout=2)
    instances = []

    class Api:
        def __init__(self):
            instances.append(self)

        def fetch(self, video_id):
            meeting.wait()
            return [_snippet(video_id)]

    _enable(monkeypatch, Api)
    first, second = await asyncio.gather(*(
        youtube.extract_transcript_async('https://youtu.be/' + name, name, max_retries=1)
        for name in ['first-fixture', 'second-fixture']
    ))
    assert first['success'] and second['success']
    assert first['transcript'] == 'first-fixture'
    assert second['transcript'] == 'second-fixture'
    assert len(instances) == 2 and instances[0] is not instances[1]


async def test_unavailable_sdk_returns_without_creating_an_api(monkeypatch):
    monkeypatch.setattr(youtube, 'YOUTUBE_AVAILABLE', False)
    monkeypatch.setattr(youtube, 'YouTubeTranscriptApi', lambda: pytest.fail('unavailable SDK created'))
    result = await youtube.extract_transcript_async('https://youtu.be/fixture', 'fixture')
    assert not result['success'] and result['transcript'] is None
