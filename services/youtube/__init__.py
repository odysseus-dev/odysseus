# services/youtube/__init__.py
"""YouTube service — transcript extraction."""

from .youtube_handler import (
    extract_transcript_async,
    extract_youtube_id,
    fetch_youtube_comments,
    format_comments_for_context,
    format_transcript_for_context,
    init_youtube,
    is_youtube_url,
)

__all__ = [
    "init_youtube",
    "is_youtube_url",
    "extract_youtube_id",
    "extract_transcript_async",
    "format_transcript_for_context",
    "fetch_youtube_comments",
    "format_comments_for_context",
]
