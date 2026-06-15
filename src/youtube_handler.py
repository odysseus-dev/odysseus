"""Compatibility wrapper for the canonical services.youtube.youtube_handler module.

Odysseus historically carried two independent copies of the YouTube handler —
one here under ``src`` and one under ``services.youtube``. They drifted: the
comment-fetch timeout fix landed only in the ``src`` copy, while ``app.py``
calls ``services.youtube.init_youtube()`` at startup. Because the chat flow
imported ``extract_transcript_async`` from ``src.youtube_handler`` (a different
module object), the ``YOUTUBE_AVAILABLE`` / ``YouTubeTranscriptApi`` globals set
by ``init_youtube`` never reached it and transcript extraction always reported
"YouTube transcript API not available".

Keep the old ``src.youtube_handler`` import path working, but make it resolve to
the single source of truth so module state and behavior can't diverge again.
"""

import importlib
import sys

# Import the canonical module directly (services.youtube.youtube_handler)
# without triggering the heavy services/__init__.py top-level imports.
_youtube_handler = importlib.import_module("services.youtube.youtube_handler")

sys.modules[__name__] = _youtube_handler
