"""Search-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the search_chats tool.
``src.tool_implementations`` re-exports these for backward compatibility.
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


async def do_search_chats(
    query: str,
    limit: int = 20,
    owner: str | None = None,
    exclude_session_id: str | None = None,
) -> Dict:
    """Search past session transcripts for the calling user's sessions only.

    Without an owner filter this used to leak EVERY user's chat history
    into the agent's `search_chats` results (v2 review HIGH-11). The
    caller in `tool_execution.execute_tool_block` now plumbs the owner
    through; legacy callers without owner pass through as before but
    will only see legacy/null-owner rows.
    """
    try:
        from src.session_search import search_session_messages

        results = search_session_messages(query, limit=limit, owner=owner)
        if exclude_session_id:
            results = [r for r in results if r.session_id != exclude_session_id]
        if not results:
            from src.session_search import search_session_titles

            results = search_session_titles(query, limit=limit, owner=owner)
            if exclude_session_id:
                results = [r for r in results if r.session_id != exclude_session_id]
        # Native callers often append the requested answer detail to a topic
        # query (for example, "roaster repair scheduling Jules"). Search the
        # topic prefix once when the exact full-text query misses; this keeps
        # chat retrieval useful without broadening into unrelated sessions.
        if not results:
            words = [word for word in query.split() if word]
            for width in (4, 3):
                if len(words) <= width:
                    continue
                prefix = " ".join(words[:width])
                results = search_session_messages(prefix, limit=limit, owner=owner)
                if exclude_session_id:
                    results = [r for r in results if r.session_id != exclude_session_id]
                if results:
                    break
        if not results:
            return {"results": f"No chats found matching \"{query}\"."}

        # Group by session to avoid duplicate links
        seen_sessions = {}
        for result in results:
            if result.session_id not in seen_sessions:
                seen_sessions[result.session_id] = result

        lines = [f"Found {len(seen_sessions)} session(s) matching \"{query}\":\n"]
        for sid, result in seen_sessions.items():
            lines.append(f"- [**{result.session_name}**](#session-{sid})")
            lines.append(f"  Open: [Open chat](#session-{sid})")
            lines.append(f"  Match ({result.role}): {result.content_snippet}")
            if result.context_before:
                before = result.context_before[-1]
                lines.append(f"  Before ({before['role']}): {before['content'][:180]}")
            if result.context_after:
                after = result.context_after[0]
                lines.append(f"  After ({after['role']}): {after['content'][:180]}")
            lines.append("")

        return {"results": "\n".join(lines)}
    except Exception as e:
        logger.error(f"search_chats failed: {e}")
        return {"error": str(e), "exit_code": 1}
