"""Regression: the YouTube handler must live in a single module.

Odysseus carried two independent copies of the handler — ``src.youtube_handler``
and ``services.youtube.youtube_handler`` — that silently drifted:

* ``app.py`` calls ``services.youtube.init_youtube()`` at startup, but the chat
  flow imported ``extract_transcript_async`` from ``src.youtube_handler``. Those
  were different module objects, so the ``YOUTUBE_AVAILABLE`` /
  ``YouTubeTranscriptApi`` globals set by ``init_youtube`` never reached the chat
  path and transcript extraction always reported "not available".
* The comment-fetch timeout fix (PR #1002) landed only in the ``src`` copy.

These tests pin the two import paths to one module object and verify the shared
state and the broadened URL parsing.
"""
import sys
import types

import pytest


def test_src_and_service_youtube_are_same_module():
    """Both historical import paths must resolve to one module object so
    behavior and module-level state cannot diverge again."""
    import src.youtube_handler as src_yt
    import services.youtube.youtube_handler as svc_yt

    assert src_yt is svc_yt


def test_init_youtube_visible_through_chat_import_path(monkeypatch):
    """init_youtube() is invoked via services.youtube (as app.py does), but the
    chat flow reads the API globals through src.youtube_handler. After
    consolidation the globals set by init must be visible on both paths."""
    import src.youtube_handler as src_yt
    from services.youtube import init_youtube

    # Pin the globals so monkeypatch restores them after the test, regardless
    # of whether youtube_transcript_api is actually installed in this env.
    monkeypatch.setattr(src_yt, "YOUTUBE_AVAILABLE", False, raising=False)
    monkeypatch.setattr(src_yt, "YouTubeTranscriptApi", None, raising=False)

    # Stand in for the real transcript package so init_youtube() succeeds
    # without a network/library dependency.
    stub = types.ModuleType("youtube_transcript_api")

    class _StubApi:
        pass

    stub.YouTubeTranscriptApi = _StubApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", stub)

    init_youtube()  # called exactly the way app.py calls it

    assert src_yt.YOUTUBE_AVAILABLE is True
    assert src_yt.YouTubeTranscriptApi is _StubApi


@pytest.mark.parametrize(
    "url,expected",
    [
        # Classic watch URLs across the youtube.com hosts.
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # YouTube Music shares the same paths and must resolve.
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # Short links.
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=ab_cd", "dQw4w9WgXcQ"),
        # Player/embed and the legacy /v/ embed.
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ/", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/v/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # Shorts and live — previously unrecognized, so the chat pipeline
        # dropped them entirely (excluded from web-fetch as a YouTube URL, but
        # no id meant no transcript fetch either).
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # Host matching is case-insensitive.
        ("https://WWW.YouTube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        # Non-video paths and non-YouTube hosts yield no id.
        ("https://www.youtube.com/", None),
        ("https://www.youtube.com/feed/subscriptions", None),
        ("https://example.com/watch?v=dQw4w9WgXcQ", None),
        ("https://vimeo.com/76979871", None),
    ],
)
def test_extract_youtube_id(url, expected):
    from src.youtube_handler import extract_youtube_id

    assert extract_youtube_id(url) == expected


def test_shorts_url_is_recognized_and_extractable():
    """A Shorts URL is treated as a YouTube link (so the chat pipeline excludes
    it from generic web-fetch). It must therefore yield an id, or the video is
    silently dropped — fetched by neither path."""
    from src.youtube_handler import is_youtube_url, extract_youtube_id

    url = "https://www.youtube.com/shorts/dQw4w9WgXcQ"
    assert is_youtube_url(url)
    assert extract_youtube_id(url) == "dQw4w9WgXcQ"
