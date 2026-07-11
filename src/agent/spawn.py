"""Spawning mechanics — subagent/peer creation, context inheritance, return format."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set

logger = logging.getLogger(__name__)


class ContextMode(Enum):
    NONE = "none"
    STATE = "state"
    FULL = "full"


@dataclass
class ReturnFormat:
    status: Optional[str] = None
    summary: Optional[str] = None
    body: str = ""


_STATUS_RE = re.compile(r"\*\*Status\*\*:\s*(success|partial|failed|blocked)", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"\*\*Summary\*\*:\s*(.+)", re.IGNORECASE)


def parse_return_header(text: str) -> ReturnFormat:
    status_match = _STATUS_RE.search(text)
    summary_match = _SUMMARY_RE.search(text)
    if not status_match:
        return ReturnFormat(body=text)
    status = status_match.group(1).lower()
    summary = summary_match.group(1).strip() if summary_match else None
    body = text
    if status_match:
        header_end = status_match.end()
        blank_match = re.search(r"\n\s*\n", text[header_end:])
        if blank_match:
            body = text[header_end + blank_match.end():].strip()
        else:
            body = text[header_end:].strip()
    return ReturnFormat(status=status, summary=summary, body=body)


@dataclass
class SpawnConfig:
    agent_type: str
    task: str
    session_id: str
    mode: str = "subagent"
    context_mode: str = "none"
    tool_allowlist: Optional[Set[str]] = None
    background: bool = False
    timeout: float = 600.0
    parent_id: Optional[str] = None
    workspace: Optional[str] = None

    @property
    def mode_enum(self):
        from src.agent.actor import ActorMode
        return ActorMode.SUBAGENT if self.mode == "subagent" else ActorMode.PEER

    @property
    def context_mode_enum(self):
        return ContextMode(self.context_mode)


RETURN_FORMAT_INSTRUCTION = """

## Return format (required)

**Status**: success | partial | failed | blocked
**Summary**: <one sentence describing what happened>

[deliverable body]

**Files touched**: <comma-separated paths or "(none)">
**Findings worth promoting**: <bullet list, or "(none)">
"""
