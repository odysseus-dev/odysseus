"""
This module intentionally imports NOTHING from the project (except
src.constants which imports nothing from src). Adding a project import here
will reintroduce the circular dependency that this module exists to break.
"""

import json

from src.constants import MAX_OUTPUT_CHARS

_mcp_manager = None
_upload_handler = None

# ---------------------------------------------------------------------------
# MCP Manager singleton
# ---------------------------------------------------------------------------

def set_mcp_manager(manager):
    """Set the global MCP manager instance."""
    global _mcp_manager
    _mcp_manager = manager

def get_mcp_manager():
    """Get the global MCP manager instance."""
    return _mcp_manager


# ---------------------------------------------------------------------------
# Shared upload lifecycle handler
# ---------------------------------------------------------------------------

def set_upload_handler(handler):
    """Register the process's UploadHandler without importing app modules."""
    global _upload_handler
    _upload_handler = handler


def get_upload_handler():
    """Return the shared UploadHandler used by route and agent writers."""
    return _upload_handler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """
    Truncate text to *limit* characters with a suffix note.

    Callers treat the result as text, so always return a string: coerce a
    non-string (None -> "", otherwise str(...)) instead of returning it raw,
    which would just move the crash downstream.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if len(text) > limit:
        # Command failures and stack traces are usually at the end. Preserve
        # both ends so truncation does not discard the only useful diagnostic.
        head_len = max(1, limit // 2)
        tail_len = max(1, limit - head_len)
        omitted = len(text) - head_len - tail_len
        return (
            text[:head_len]
            + f"\n... ({omitted} chars omitted; {len(text)} total) ...\n"
            + text[-tail_len:]
        )
    return text


def _parse_tool_args(content):
    """Parse a tool-call argument blob.

    Accepts either a JSON string or an already-decoded dict. Unwraps the
    common `{"body": {...}}` envelope that smaller models emit when they
    read tool descriptions like "Body is JSON: {...}" literally and
    pass `body` as a field name rather than treating it as a noun.

    Returns a dict on success, raises ValueError on bad JSON.
    """
    if isinstance(content, str):
        try:
            args = json.loads(content) if content.strip() else {}
            if not isinstance(args, dict):
                args = {}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(str(e))
    elif isinstance(content, dict):
        args = content
    else:
        args = {}
    # Unwrap {"body": {...}} envelope, but only if `body` is the sole key
    # and points at a dict. We don't want to clobber a legitimate `body`
    # field on tools where it's a real arg (e.g. send_email body text).
    if (
        isinstance(args, dict)
        and len(args) == 1
        and "body" in args
        and isinstance(args["body"], dict)
        and "action" in args["body"]  # extra safety: only unwrap if the inner dict looks like a tool call
    ):
        args = args["body"]
    return args
