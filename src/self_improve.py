"""
self_improve.py

Self-improvement engine for the Odysseus agent.

Collects failure events from agent turns, analyzes patterns across
sessions, proposes code fixes, and automatically generates PRs.

Architecture:
  1. Event collection — hooks into post-chat processing
  2. Storage — JSON log in DATA_DIR/self_improve/
  3. Pattern analysis — groups similar failures
  4. Fix proposals — generates suggested code patches
  5. PR generation — creates GitHub PRs via CLI

Triggered:
  - After every agent turn with errors/failures
  - Periodically (daily) for pattern analysis and PR generation
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.constants import DATA_DIR

logger = logging.getLogger(__name__)

_SELF_IMPROVE_DIR = Path(DATA_DIR) / "self_improve"
_FAILURES_FILE = _SELF_IMPROVE_DIR / "failures.json"
_PATTERNS_FILE = _SELF_IMPROVE_DIR / "patterns.json"
_FIXES_FILE = _SELF_IMPROVE_DIR / "fixes.json"
_ANALYSIS_INTERVAL = 86400  # 24 hours between pattern analyses
_EVENT_COUNT_THRESHOLD = 3  # Min failures before proposing a fix

# ---------------------------------------------------------------------------
# Known fix templates — maps error patterns to code fix strategies
# ---------------------------------------------------------------------------

FIX_STRATEGIES: Dict[str, Dict] = {
    "missing_schema": {
        "description": "Tool has RAG description but no OpenAI function schema",
        "check": lambda tool_name: _tool_has_rag_but_no_schema(tool_name),
        "fix_template": (
            "Add OpenAI function schema for `{tool_name}` in `src/tool_schemas.py`.\n"
            "The tool has a RAG description in `src/tool_index.py` but no corresponding\n"
            "entry in `FUNCTION_TOOL_SCHEMAS`, making it unreachable via native function calling."
        ),
    },
    "missing_tool_tag": {
        "description": "Tool has a schema but is missing from TOOL_TAGS",
        "check": lambda tool_name: _tool_has_schema_but_no_tag(tool_name),
        "fix_template": (
            "Add `{tool_name}` to TOOL_TAGS in `src/agent_tools/__init__.py`.\n"
            "The tool has an OpenAI function schema but is rejected as 'Unknown function call'\n"
            "because it's not in the TOOL_TAGS set."
        ),
    },
    "missing_rag_description": {
        "description": "Tool has schema and tag but no RAG description",
        "check": lambda tool_name: _tool_has_schema_but_no_rag(tool_name),
        "fix_template": (
            "Add RAG description for `{tool_name}` in `src/tool_index.py` BUILTIN_TOOL_DESCRIPTIONS.\n"
            "The tool has a schema and TOOL_TAGS entry but no searchable description,\n"
            "so the RAG retriever never surfaces it."
        ),
    },
    "path_confinement": {
        "description": "File operation rejected due to path outside allowed roots",
        "check": lambda _: True,
        "fix_template": (
            "Agent file operations are being blocked by path confinement.\n"
            "Verify `ODYSSEUS_WORKSPACE` or `ODYSSEUS_DEVELOPER_MODE` is set,\n"
            "or add the needed directory to `tool_path_extra_roots` in Settings."
        ),
    },
    "unknown_action": {
        "description": "Agent used an unsupported action on a management tool",
        "check": lambda _: True,
        "fix_template": (
            "Agent called `{tool_name}` with action='{action}' which isn't supported.\n"
            "Either add the action to the tool's implementation or update the\n"
            "tool description to clearly list supported actions."
        ),
    },
    "unknown_tool": {
        "description": "Agent tried to use a tool that doesn't exist",
        "check": lambda _: True,
        "fix_template": (
            "Agent tried to call `{tool_name}` which doesn't exist.\n"
            "Consider adding this tool or updating the prompt to guide the\n"
            "agent toward existing alternatives."
        ),
    },
}

# ---------------------------------------------------------------------------
# Failure event data structures
# ---------------------------------------------------------------------------

class FailureEvent:
    """A single failure event from an agent turn."""

    def __init__(
        self,
        tool_name: str,
        error_type: str,
        error_message: str,
        session_id: str = "",
        action: str = "",
        timestamp: Optional[float] = None,
    ):
        self.tool_name = tool_name
        self.error_type = error_type
        self.error_message = error_message
        self.session_id = session_id
        self.action = action
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "error_type": self.error_type,
            "error_message": self.error_message[:500],
            "session_id": self.session_id,
            "action": self.action,
            "timestamp": self.timestamp,
            "time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FailureEvent":
        return cls(
            tool_name=d.get("tool_name", ""),
            error_type=d.get("error_type", ""),
            error_message=d.get("error_message", ""),
            session_id=d.get("session_id", ""),
            action=d.get("action", ""),
            timestamp=d.get("timestamp"),
        )

    @property
    def pattern_key(self) -> str:
        """Key used to group similar failures."""
        return f"{self.tool_name}:{self.error_type}"


class FailurePattern:
    """A recurring failure pattern across multiple sessions."""

    def __init__(self, key: str, events: List[FailureEvent]):
        self.key = key
        self.tool_name = events[0].tool_name if events else ""
        self.error_type = events[0].error_type if events else ""
        self.count = len(events)
        self.sessions: Set[str] = {e.session_id for e in events if e.session_id}
        self.first_seen = min(e.timestamp for e in events)
        self.last_seen = max(e.timestamp for e in events)
        self.sample_errors = list({e.error_message[:200] for e in events})[:3]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "tool_name": self.tool_name,
            "error_type": self.error_type,
            "count": self.count,
            "unique_sessions": len(self.sessions),
            "first_seen": datetime.fromtimestamp(self.first_seen, tz=timezone.utc).isoformat(),
            "last_seen": datetime.fromtimestamp(self.last_seen, tz=timezone.utc).isoformat(),
            "sample_errors": self.sample_errors,
        }

    @property
    def strategy_key(self) -> Optional[str]:
        """Map this pattern to a fix strategy."""
        if self.error_type == "missing_schema":
            return "missing_schema"
        if self.error_type == "missing_tool_tag":
            return "missing_tool_tag"
        if self.error_type == "missing_rag_description":
            return "missing_rag_description"
        if self.error_type == "path_confinement":
            return "path_confinement"
        if self.error_type == "unknown_action":
            return "unknown_action"
        if self.error_type == "unknown_tool":
            return "unknown_tool"
        return None


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------

def classify_error(tool_name: str, error_message: str) -> str:
    """Classify the error into a known error type."""
    msg = error_message.lower()

    if "unknown function call" in msg or "unknown tool" in msg:
        return "unknown_tool"
    if "outside the allowed roots" in msg or "outside the workspace" in msg:
        return "path_confinement"
    if "unknown action" in msg:
        return "unknown_action"
    if "no openai schema" in msg or "unreachable via native" in msg:
        return "missing_schema"
    if "not in tool_tags" in msg or "rejected as unknown" in msg:
        return "missing_tool_tag"
    if "no rag description" in msg or "never surfaced" in msg:
        return "missing_rag_description"
    if "i don't have" in msg or "not in my available" in msg:
        return "tool_not_available"
    if "permission denied" in msg or "access denied" in msg:
        return "permission_denied"
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "syntax error" in msg or "invalid" in msg:
        return "syntax_error"
    return "unknown_error"


# ---------------------------------------------------------------------------
# Helpers for fix strategy checks
# ---------------------------------------------------------------------------

def _tool_has_rag_but_no_schema(tool_name: str) -> bool:
    """Check if a tool has a RAG description but no schema."""
    try:
        from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
        has_rag = tool_name in BUILTIN_TOOL_DESCRIPTIONS
        has_schema = any(
            s.get("function", {}).get("name") == tool_name
            for s in FUNCTION_TOOL_SCHEMAS
        )
        return has_rag and not has_schema
    except Exception:
        return False


def _tool_has_schema_but_no_tag(tool_name: str) -> bool:
    """Check if a tool has a schema but missing from TOOL_TAGS."""
    try:
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
        from src.agent_tools import TOOL_TAGS
        has_schema = any(
            s.get("function", {}).get("name") == tool_name
            for s in FUNCTION_TOOL_SCHEMAS
        )
        return has_schema and tool_name not in TOOL_TAGS
    except Exception:
        return False


def _tool_has_schema_but_no_rag(tool_name: str) -> bool:
    """Check if a tool has schema + tag but no RAG description."""
    try:
        from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
        from src.agent_tools import TOOL_TAGS
        from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
        has_schema = any(
            s.get("function", {}).get("name") == tool_name
            for s in FUNCTION_TOOL_SCHEMAS
        )
        has_tag = tool_name in TOOL_TAGS
        has_rag = tool_name in BUILTIN_TOOL_DESCRIPTIONS
        return has_schema and has_tag and not has_rag
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Event persistence
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    """Create self-improve data directory if it doesn't exist."""
    _SELF_IMPROVE_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> list:
    """Read a JSON array from file, return empty list if missing/corrupt."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write_json(path: Path, data: list) -> None:
    """Write a JSON array to file."""
    _ensure_dir()
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def log_failure(
    tool_name: str,
    error_message: str,
    session_id: str = "",
    action: str = "",
    error_type: str = "",
) -> None:
    """Log an agent tool failure event for later analysis."""
    try:
        error_type = error_type or classify_error(tool_name, error_message)
        event = FailureEvent(
            tool_name=tool_name,
            error_type=error_type,
            error_message=error_message,
            session_id=session_id,
            action=action,
        )
        events = _read_json(_FAILURES_FILE)
        events.append(event.to_dict())
        # Keep last 1000 events to prevent unbounded growth.
        if len(events) > 1000:
            events = events[-1000:]
        _write_json(_FAILURES_FILE, events)
        logger.debug(
            "[self-improve] logged failure: %s/%s (total=%d)",
            tool_name, error_type, len(events),
        )
    except Exception as e:
        logger.warning("[self-improve] failed to log failure: %s", e)


def get_failures(tool_name: Optional[str] = None, error_type: Optional[str] = None) -> List[FailureEvent]:
    """Get all logged failures, optionally filtered."""
    raw = _read_json(_FAILURES_FILE)
    events = [FailureEvent.from_dict(d) for d in raw]
    if tool_name:
        events = [e for e in events if e.tool_name == tool_name]
    if error_type:
        events = [e for e in events if e.error_type == error_type]
    return events


# ---------------------------------------------------------------------------
# Pattern analysis
# ---------------------------------------------------------------------------

def analyze_patterns(force: bool = False) -> List[FailurePattern]:
    """Analyze failure events for recurring patterns.

    Returns patterns that meet the count threshold. Results are cached
    in patterns.json and only re-analyzed after ANALYSIS_INTERVAL
    unless forced.
    """
    _ensure_dir()

    # Check if we analyzed recently
    if not force and _PATTERNS_FILE.exists():
        mtime = _PATTERNS_FILE.stat().st_mtime
        if time.time() - mtime < _ANALYSIS_INTERVAL:
            logger.debug("[self-improve] skipping analysis — already recent")
            return []

    events = get_failures()
    if not events:
        logger.debug("[self-improve] no failure events to analyze")
        return []

    # Group by pattern key
    groups: Dict[str, List[FailureEvent]] = defaultdict(list)
    for event in events:
        groups[event.pattern_key].append(event)

    # Build patterns meeting threshold
    patterns = []
    for key, group in groups.items():
        if len(group) >= _EVENT_COUNT_THRESHOLD:
            pattern = FailurePattern(key, group)
            patterns.append(pattern)

    patterns.sort(key=lambda p: p.count, reverse=True)

    # Persist patterns
    _write_json(_PATTERNS_FILE, [p.to_dict() for p in patterns])

    if patterns:
        logger.info(
            "[self-improve] found %d patterns from %d events",
            len(patterns), len(events),
        )
        for p in patterns[:5]:
            logger.info(
                "[self-improve]   %s: %d occurrences across %d sessions",
                p.key, p.count, len(p.sessions),
            )

    return patterns


def get_patterns() -> List[dict]:
    """Get cached analysis patterns."""
    return _read_json(_PATTERNS_FILE)


# ---------------------------------------------------------------------------
# Fix proposal generation
# ---------------------------------------------------------------------------

def propose_fix(pattern: FailurePattern) -> Optional[dict]:
    """Generate a fix proposal for a failure pattern.

    Returns None if no strategy matches, or a dict with:
      - title: short fix description
      - tool_name: affected tool
      - body: detailed fix instructions
      - files_affected: list of file paths that need changes
      - error_type: the error type
    """
    strategy_key = pattern.strategy_key
    if not strategy_key:
        return None

    strategy = FIX_STRATEGIES.get(strategy_key)
    if not strategy:
        return None

    # Verify the tool still has this issue
    if not strategy["check"](pattern.tool_name):
        return None

    body = strategy["fix_template"].format(
        tool_name=pattern.tool_name,
        action=(pattern.sample_errors[0] if pattern.sample_errors else ""),
    )

    files = []
    if strategy_key == "missing_schema":
        files = ["src/tool_schemas.py"]
    elif strategy_key == "missing_tool_tag":
        files = ["src/agent_tools/__init__.py"]
    elif strategy_key == "missing_rag_description":
        files = ["src/tool_index.py"]
    elif strategy_key in ("path_confinement", "unknown_action", "unknown_tool"):
        files = ["src/tool_execution.py", "src/agent_loop.py"]

    return {
        "title": f"fix({pattern.tool_name}): {strategy['description']}",
        "tool_name": pattern.tool_name,
        "body": (
            f"## Auto-detected pattern\n\n"
            f"**Pattern**: {pattern.key}\n"
            f"**Occurrences**: {pattern.count} across {len(pattern.sessions)} sessions\n"
            f"**First seen**: {pattern.first_seen}\n"
            f"**Last seen**: {pattern.last_seen}\n\n"
            f"## Proposed fix\n\n"
            f"{body}\n\n"
            f"## Sample errors\n\n"
            + "\n".join(f"- `{e}`" for e in pattern.sample_errors[:3])
        ),
        "files_affected": files,
        "error_type": pattern.error_type,
    }


def propose_all_fixes(force: bool = False) -> List[dict]:
    """Propose fixes for all detected patterns."""
    patterns = analyze_patterns(force=force)
    fixes = []
    already_proposed = {
        f.get("key") for f in _read_json(_FIXES_FILE)
    }

    for pattern in patterns:
        if pattern.key in already_proposed:
            continue
        fix = propose_fix(pattern)
        if fix:
            fix["key"] = pattern.key
            fixes.append(fix)

    if fixes:
        existing = _read_json(_FIXES_FILE)
        existing.extend(fixes)
        _write_json(_FIXES_FILE, existing)
        logger.info("[self-improve] proposed %d new fixes", len(fixes))

    return fixes


# ---------------------------------------------------------------------------
# PR generation
# ---------------------------------------------------------------------------

def create_fix_pr(fix: dict, base_branch: str = "dev") -> Optional[str]:
    """Create a GitHub PR for a proposed fix.

    Uses the gh CLI. The fix branch is created, changes are described
    in the PR body, and the PR is opened. Returns the PR URL or None.
    """
    branch_name = f"self-improve/{fix['tool_name']}-{fix['error_type']}"
    title = fix["title"]
    body = fix["body"]

    try:
        # Check if branch already exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("[self-improve] branch %s already exists, skipping PR", branch_name)
            return None

        # Create branch
        subprocess.run(
            ["git", "checkout", "-b", branch_name, base_branch],
            capture_output=True, text=True, check=True,
        )

        # Create an empty commit (human reviewer implements the fix)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", title + "\n\n" + body],
            capture_output=True, text=True, check=True,
        )

        # Push
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            capture_output=True, text=True, check=True,
        )

        # Create PR
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--base", base_branch,
                "--title", title,
                "--body", body,
            ],
            capture_output=True, text=True, check=True,
        )

        # Return to dev
        subprocess.run(
            ["git", "checkout", base_branch],
            capture_output=True, text=True,
        )

        pr_url = result.stdout.strip()
        logger.info("[self-improve] created PR: %s", pr_url)
        return pr_url

    except subprocess.CalledProcessError as e:
        logger.error(
            "[self-improve] PR creation failed: %s\nstderr: %s",
            e, getattr(e, "stderr", ""),
        )
        # Try to return to base branch
        subprocess.run(
            ["git", "checkout", base_branch],
            capture_output=True, text=True,
        )
        return None
    except Exception as e:
        logger.error("[self-improve] unexpected error: %s", e)
        return None


def auto_improve(create_prs: bool = False, force: bool = False) -> List[dict]:
    """Run the full self-improvement cycle.

    1. Analyze failure patterns
    2. Propose fixes
    3. Optionally create PRs

    Returns list of fixes proposed or created.
    """
    _ensure_dir()

    fixes = propose_all_fixes(force=force)
    if not fixes:
        logger.info("[self-improve] no new fixes to propose")
        return []

    results = []
    for fix in fixes:
        result = {"fix": fix["title"], "tool": fix["tool_name"]}
        if create_prs:
            pr_url = create_fix_pr(fix)
            if pr_url:
                result["pr_url"] = pr_url
                logger.info("[self-improve] PR created: %s", pr_url)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Agent-turn hook
# ---------------------------------------------------------------------------

async def maybe_log_tool_failure(
    result: dict,
    tool_name: str = "",
    session_id: str = "",
) -> None:
    """Hook called after tool execution. Logs failures for pattern analysis.

    Called from agent_loop.py after a tool block executes. Analyses the
    result for error signals and classifies them.
    """
    if not result:
        return

    error_msg = result.get("error", "")
    if not error_msg:
        return

    action = ""
    # Try to extract action from JSON content
    try:
        if result.get("needs_approval"):
            action = "destructive_blocked"
        elif result.get("ask_user"):
            action = "ask_user"
    except Exception:
        pass

    error_type = classify_error(tool_name, error_msg)
    log_failure(
        tool_name=tool_name,
        error_message=error_msg,
        session_id=session_id,
        action=action,
        error_type=error_type,
    )


async def periodic_self_improve() -> Optional[List[dict]]:
    """Run the self-improvement cycle (called daily by task scheduler).

    Analyses patterns and proposes fixes, but does NOT create PRs
    automatically — those require maintainer approval.
    """
    try:
        fixes = auto_improve(create_prs=False, force=True)
        if fixes:
            logger.info(
                "[self-improve] cycle complete: %d fixes proposed",
                len(fixes),
            )
        return fixes
    except Exception as e:
        logger.error("[self-improve] periodic cycle failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Status / diagnostic
# ---------------------------------------------------------------------------

def get_status() -> dict:
    """Return current self-improvement status for diagnostics."""
    failures = _read_json(_FAILURES_FILE)
    patterns = _read_json(_PATTERNS_FILE)
    fixes = _read_json(_FIXES_FILE)

    # Count by error type
    by_type = defaultdict(int)
    for f in failures:
        by_type[f.get("error_type", "unknown")] += 1

    return {
        "total_failures": len(failures),
        "total_patterns": len(patterns),
        "total_fixes_proposed": len(fixes),
        "failures_by_type": dict(by_type),
        "top_patterns": [
            {
                "key": p["key"],
                "count": p["count"],
                "sessions": p["unique_sessions"],
            }
            for p in sorted(patterns, key=lambda x: x["count"], reverse=True)[:5]
        ],
    }
