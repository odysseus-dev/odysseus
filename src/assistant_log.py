"""
assistant_log.py

Global utility to post messages to the personal assistant's chat session.
Any part of the codebase can call log_to_assistant() to surface events,
notifications, and results in the assistant's unified activity feed.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Session manager reference — set by app.py after initialization
_session_manager = None


def set_session_manager(sm):
    global _session_manager
    _session_manager = sm


# Pattern callers use to embed a category in the content (legacy):
#   "**[Download]** Started downloading ..."
# We extract that into structured metadata so the UI can color-code by
# category without parsing markdown.
_LEGACY_TAG_RE = re.compile(r"^\s*\*\*\[([^\]]{1,40})\]\*\*\s*")


def log_to_assistant(
    owner: str,
    content: str,
    role: str = "assistant",
    *,
    category: str | None = None,
):
    """Legacy no-op.

    Older builds wrote system/task activity into a favorited Assistant chat
    session. Activity now lives in Tasks/notifications, so keep this shim for
    callers while preventing sidebar-log sessions from being created or filled.
    """
    logger.debug("log_to_assistant ignored legacy activity category=%r owner=%r", category, owner)
    return
