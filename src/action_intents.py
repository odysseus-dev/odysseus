"""Lightweight routing hints for chat requests that need tools.

These patterns are intentionally conservative. They only promote plain chat
to agent mode when the user asks the assistant to take an action, not when the
user asks how a feature works.
"""

from __future__ import annotations

import re
from typing import Iterable, Pattern


_ACTION_QUESTION = r"\b(?:can|could|would|will)\s+you\s+"
_PLEASE = r"^\s*(?:please\s+)?"

_CALENDAR_ACTION = r"(?:add|create|schedule|book|put|set\s+up|make)"
_CALENDAR_THING = r"(?:calendar|calendar\s+(?:entry|item)|event|meeting|appointment|entry|call)"

_PANEL = (
    r"(?:calendar|notes?|inbox|email|mail|documents?|docs|library|gallery|"
    r"settings|cookbook|sessions?|chats?|skills|memories|memory|brain)"
)

_TOOL_INTENT_PATTERNS: tuple[Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        # Calendar/event creation. Covers "Can you add an entry to my
        # calendar?" and imperatives like "add lunch to my calendar".
        rf"{_ACTION_QUESTION}{_CALENDAR_ACTION}\b.{{0,120}}\b{_CALENDAR_THING}\b",
        rf"{_PLEASE}{_CALENDAR_ACTION}\b.{{0,120}}\b(?:to|on|in|into|for)\s+(?:my\s+|the\s+|this\s+)?calendar\b",
        rf"{_PLEASE}{_CALENDAR_ACTION}\s+(?:a\s+|an\s+)?(?:calendar\s+)?(?:event|meeting|appointment|entry|item|call)\b",
        r"\bput\s+.+\bon\s+(?:my\s+)?calendar\b",

        # Notes, todos, checklists, and reminders.
        r"\bremind\s+me\b",
        rf"{_ACTION_QUESTION}(?:add|create|make|take|jot|write\s+down|set)\b.{{0,120}}\b(?:note|todo|task|checklist|reminder)\b",
        rf"{_PLEASE}(?:add|create|make)\s+(?:a\s+|an\s+)?(?:todo|task|reminder|note|checklist)\b",
        rf"{_PLEASE}(?:take|jot|write\s+down)\s+(?:a\s+|an\s+)?note\b",
        rf"{_PLEASE}(?:add|jot|write\s+down)\b.{{0,120}}\b(?:to|in|into)\s+(?:my\s+|the\s+)?(?:todo(?:\s+list)?|task\s+list|notes?|checklist)\b",
        rf"{_PLEASE}set\s+(?:a\s+)?reminder\b",
        rf"{_ACTION_QUESTION}set\s+(?:a\s+)?reminder\b",

        # Email actions.
        rf"{_ACTION_QUESTION}(?:send|write|reply|email|message|archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox|unread|read)\b",
        rf"{_PLEASE}(?:send|write|reply)\b.{{0,120}}\b(?:emails?|mail|messages?)\b",
        rf"{_PLEASE}(?:archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox)\b",
        r"\b(?:send|write|reply)\s+(?:an?\s+)?(?:email|message|mail)\b",
        r"\bemail\s+\w+\b",
        r"\bcheck\s+(?:my\s+)?(?:email|inbox|mail)\b",
        r"\bunread\s+(?:email|mail)s?\b",

        # UI/control-plane actions that should open panels or flip toggles.
        rf"{_PLEASE}(?:open|show|bring\s+up)\s+(?:me\s+)?(?:my\s+|the\s+)?{_PANEL}\b",
        r"\b(?:disable|enable|turn\s+(?:on|off))\s+(?:the\s+)?(?:shell|search|web|browser|documents?|memory|skills|images?|calendar|email|mail|research|incognito)\b",

        # Deep research jobs, not quick conceptual mentions of research.
        rf"{_PLEASE}(?:research|deep\s+dive|look\s+into|investigate)\s+.+",
        rf"{_ACTION_QUESTION}(?:research|do\s+research|deep\s+dive|look\s+into|investigate)\s+.+",

        # Shell / remote-host intent.
        r"\bssh\s+(?:in)?to\b",
        r"\bssh\s+\w+",
        r"\b(run|execute)\s+.{1,40}\bon\s+\w+",
        r"\b(can|could|please|would)\s+you\s+(run|execute|exec)\b",
        # Shell verbs only count in imperative position (start of message,
        # optionally after "please") or as a "can you ..." request. A bare
        # word match promoted informational questions ("What does the grep
        # command do?") and incidental uses ("My cat ate my homework").
        rf"{_PLEASE}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+",
        rf"{_ACTION_QUESTION}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+",
        r"\b(check|see)\s+(if|whether|what)\s+.{1,40}\b(running|process|service|port|file|exists?)\b",
    )
)


def message_needs_tools(text: str, patterns: Iterable[Pattern[str]] = _TOOL_INTENT_PATTERNS) -> bool:
    """Return True when a plain chat message should be promoted to agent mode."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in patterns)
