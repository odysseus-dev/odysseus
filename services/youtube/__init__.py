"""YouTube service — transcript extraction."""

import sys

from src import youtube_handler as _youtube_handler

sys.modules[__name__ + ".youtube_handler"] = _youtube_handler
youtube_handler = _youtube_handler

init_youtube = _youtube_handler.init_youtube
is_youtube_url = _youtube_handler.is_youtube_url
extract_youtube_id = _youtube_handler.extract_youtube_id
extract_transcript_async = _youtube_handler.extract_transcript_async
format_transcript_for_context = _youtube_handler.format_transcript_for_context
fetch_youtube_comments = _youtube_handler.fetch_youtube_comments
format_comments_for_context = _youtube_handler.format_comments_for_context

__all__ = [
    "init_youtube",
    "is_youtube_url",
    "extract_youtube_id",
    "extract_transcript_async",
    "format_transcript_for_context",
    "fetch_youtube_comments",
    "format_comments_for_context",
]
