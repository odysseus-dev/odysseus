import logging

logger = logging.getLogger(__name__)


class SearchChatsTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Search past session transcripts for the calling user's sessions only.
        Without an owner filter this used to leak EVERY user's chat history
        into the agent's `search_chats` results (v2 review HIGH-11). The
        caller in `tool_execution.execute_tool_block` now plumbs the owner
        through; legacy callers without owner pass through as before but
        will only see legacy/null-owner rows."""
        owner = ctx.get("owner")
        query = content.split("\n")[0].strip()
        try:
            from src.session_search import search_session_messages

            results = search_session_messages(query, limit=20, owner=owner)
            if not results:
                return {"results": f'No chats found matching "{query}".'}
            
            # Group by session to avoid duplicate links
            seen_sessions = {}
            for result in results:
                if result.session_id not in seen_sessions:
                    seen_sessions[result.session_id] = result
                
            lines = [f'Found {len(seen_sessions)} session(s) matching "{query}":\n']
            for sid, result in seen_sessions.items():
                lines.append(f"- **{result.session_name}** (#{sid})")
                lines.append(f"  Link: [Open chat](#{sid})")
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
