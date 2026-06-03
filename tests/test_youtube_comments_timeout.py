"""Regression: fetch_youtube_comments must actually honour its timeout.

The timeout previously wrapped ``create_subprocess_exec`` (which returns as soon
as the child is spawned) instead of ``proc.communicate()`` (the step that waits
for yt-dlp to finish). A hung yt-dlp would therefore block forever and the
``except asyncio.TimeoutError`` handler was unreachable. The wait must be bound
to communicate(), and the child killed when it overruns.
"""
import asyncio

from src import youtube_handler


def test_comment_fetch_honours_timeout(monkeypatch):
    monkeypatch.setattr(youtube_handler, "_find_ytdlp", lambda: "yt-dlp")

    killed = {"value": False}

    class HangingProc:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(30)  # far longer than the test timeout
            return (b"", b"")

        def kill(self):
            killed["value"] = True

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        return HangingProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )

    result = asyncio.run(
        youtube_handler.fetch_youtube_comments("vid123", timeout=0.1)
    )

    assert result["success"] is False
    assert "timed out" in result["error"].lower()
    assert result["comments"] == []
    # The overrunning child must be killed, not left running.
    assert killed["value"] is True
