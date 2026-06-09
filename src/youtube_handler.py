"""
YouTube handling — transcript extraction, comment fetching (yt-dlp),
and context formatting for LLM injection. Used by chat_handler.py.
"""

import asyncio
import json
import logging
import shutil
import sys
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

YOUTUBE_INSTRUCTION_PROMPT = """When the user shares a YouTube video, respond with a structured breakdown:

1. **Summary** — Concise overview of the video's content and main thesis (2-4 sentences)
2. **Key Points** — Bullet list of the most important topics, arguments, or moments
3. **Notable Timestamps** — If timestamps are available from the transcript, highlight 3-5 interesting moments with their approximate timestamps (e.g. "03:45 — discusses X")
4. **Audience Reception** — If comments are available, summarize what viewers think: general sentiment, top reactions, any debate or controversy

Keep it conversational and concise. Do NOT web search for this video — use only the transcript and comments provided."""

# ---------------------------------------------------------------------------
# Init / helpers
# ---------------------------------------------------------------------------

# Will be set at startup by init_youtube()
YouTubeTranscriptApi = None
YOUTUBE_AVAILABLE = False


def _find_ytdlp() -> str:
    """Find the yt-dlp binary: venv bin first, then system PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which("yt-dlp")
    return found or "yt-dlp"


def init_youtube():
    """Import and cache the YouTube transcript API."""
    global YouTubeTranscriptApi, YOUTUBE_AVAILABLE
    try:
        from youtube_transcript_api import YouTubeTranscriptApi as _Api
        YouTubeTranscriptApi = _Api
        YOUTUBE_AVAILABLE = True
        logger.info("YouTube transcript API available")
    except ImportError as e:
        logger.warning(f"youtube-transcript-api not installed: {e}")
        YOUTUBE_AVAILABLE = False


def is_youtube_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    return "youtube.com" in url or "youtu.be" in url


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            params = urllib.parse.parse_qs(parsed.query)
            if "v" in params:
                return params["v"][0]
        elif parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[-1]
    elif parsed.hostname == "youtu.be":
        return parsed.path[1:]
    return None


async def extract_transcript_async(
    url: str, video_id: str, max_retries: int = 3
) -> Dict[str, Any]:
    """
    Async YouTube transcript extraction with retries.

    Args:
        url: Full YouTube URL
        video_id: Extracted video ID
        max_retries: Number of attempts

    Returns:
        Dict with success/error/transcript keys
    """
    if not YOUTUBE_AVAILABLE or YouTubeTranscriptApi is None:
        return {"success": False, "error": "YouTube transcript API not available", "transcript": None}

    for attempt in range(max_retries):
        try:
            api = YouTubeTranscriptApi()
            transcript = api.fetch(video_id)
            transcript_list = list(transcript)

            formatted = []
            for snippet in transcript_list:
                text = snippet.text.strip()
                if not text:
                    continue
                start = snippet.start
                formatted.append({
                    "text": text,
                    "start": start,
                    "duration": snippet.duration,
                    "timestamp": f"{int(start // 60):02d}:{int(start % 60):02d}",
                })

            full_text = " ".join(e["text"] for e in formatted)
            max_len = 8000
            if len(full_text) > max_len:
                full_text = full_text[:max_len] + "... [transcript truncated]"

            return {
                "success": True,
                "transcript": full_text,
                "video_id": video_id,
                "language": "en",
                "is_generated": False,
                "segments": formatted,
            }
        except Exception as e:
            logger.warning(f"Transcript attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))

    return {"success": False, "error": f"Failed after {max_retries} attempts", "transcript": None}


def format_transcript_for_context(
    transcript_data: Dict[str, Any], url: str,
    title: str = "", channel: str = ""
) -> str:
    """Format transcript data for inclusion in LLM context."""
    if not transcript_data.get("success"):
        header = ""
        if title:
            header = f" \"{title}\""
            if channel:
                header += f" by {channel}"
        return f"\n[YouTube Video{header}: Transcript unavailable ({transcript_data.get('error', 'Unknown error')}). Use the comments below if available, do NOT web search for this video.]"

    transcript = transcript_data.get("transcript", "")
    video_id = transcript_data.get("video_id", "")
    language = transcript_data.get("language", "unknown")
    is_generated = transcript_data.get("is_generated", False)
    segments = transcript_data.get("segments", [])

    ctx = "\n[YOUTUBE VIDEO TRANSCRIPT]\n"
    if title:
        ctx += f"Title: {title}\n"
    if channel:
        ctx += f"Channel: {channel}\n"
    ctx += f"Video ID: {video_id}\n"
    ctx += f"Language: {language}\n"
    ctx += f"Source: {'Auto-generated' if is_generated else 'Manual'}\n"
    ctx += f"URL: {url}\n\n"
    # Include timestamped segments for the LLM to reference
    if segments:
        ctx += "Timestamped Transcript:\n"
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            ctx += f"[{seg['timestamp']}] {seg['text']}\n"
        # Check length — fall back to plain text if too long
        if len(ctx) > 12000:
            ctx = ctx[:ctx.index("Timestamped Transcript:\n")]
            ctx += "Transcript:\n"
            ctx += transcript
    else:
        ctx += "Transcript:\n"
        ctx += transcript
    ctx += "\n[END TRANSCRIPT]\n"
    return ctx


async def fetch_youtube_comments(
    video_id: str, max_comments: int = 25, timeout: int = 30
) -> Dict[str, Any]:
    """Fetch top comments for a YouTube video using yt-dlp.

    Returns dict with 'success', 'comments' list, 'error'.
    """
    try:
        cmd = [
            _find_ytdlp(),
            "--skip-download",
            "--write-comments",
            "--extractor-args", f"youtube:max_comments={max_comments},all,100,0",
            "--dump-json",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Bound the wait on the process actually finishing, not on spawning it.
        # create_subprocess_exec returns as soon as the child starts, so wrapping
        # it in wait_for never enforces the timeout — proc.communicate() is the
        # blocking step. Kill and reap the child if it overruns so it does not
        # linger after we return.
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        if proc.returncode != 0:
            return {"success": False, "error": f"yt-dlp failed: {stderr.decode()[:200]}", "comments": []}

        data = json.loads(stdout.decode())
        title = data.get("title", "")
        channel = data.get("channel", "") or data.get("uploader", "")
        raw_comments = data.get("comments", [])

        comments = []
        for c in raw_comments[:max_comments]:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            comments.append({
                "author": c.get("author", "Unknown"),
                "text": text,
                "likes": c.get("like_count", 0),
            })

        # Sort by likes descending — most popular comments first
        comments.sort(key=lambda x: x.get("likes", 0), reverse=True)

        return {"success": True, "comments": comments, "count": len(comments),
                "title": title, "channel": channel}

    except asyncio.TimeoutError:
        logger.warning(f"Comment fetch timed out for {video_id}")
        return {"success": False, "error": "Comment fetch timed out", "comments": []}
    except FileNotFoundError:
        logger.warning("yt-dlp not installed — cannot fetch comments")
        return {"success": False, "error": "yt-dlp not installed", "comments": []}
    except Exception as e:
        logger.warning(f"Failed to fetch comments for {video_id}: {e}")
        return {"success": False, "error": str(e), "comments": []}


def format_comments_for_context(comments_data: Dict[str, Any], url: str) -> str:
    """Format YouTube comments for inclusion in LLM context."""
    if not comments_data.get("success") or not comments_data.get("comments"):
        return ""

    comments = comments_data["comments"]
    ctx = f"\n[YOUTUBE VIDEO COMMENTS — Top {len(comments)} by popularity]\n"
    ctx += f"URL: {url}\n\n"

    for i, c in enumerate(comments, 1):
        likes = c.get("likes", 0)
        likes_str = f" [{likes} likes]" if likes else ""
        ctx += f"{i}. @{c['author']}{likes_str}: {c['text']}\n\n"

    if len(ctx) > 4000:
        ctx = ctx[:4000] + "\n[Comments truncated]\n"

    ctx += "[END COMMENTS]\n"
    return ctx
