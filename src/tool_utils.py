"""
This module intentionally imports NOTHING from the project (except
src.constants which imports nothing from src). Adding a project import here
will reintroduce the circular dependency that this module exists to break.
"""

from src.constants import MAX_OUTPUT_CHARS

_mcp_manager = None

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
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text
