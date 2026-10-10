"""productivity_tools.py - agent tools for personal productivity data.

Registers manage_notes in TOOL_HANDLERS as part of the tool -> registry
migration (#3629). The implementation stays in src.tools.notes; the handler
class adapts it to the registry's execute(content, ctx) shape and threads the
caller's owner from ctx.
"""
from typing import Dict

from src.tools.notes import do_manage_notes


# ---------------------------------------------------------------------------
# Handler classes registered in TOOL_HANDLERS
# ---------------------------------------------------------------------------

class ManageNotesTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await do_manage_notes(content, owner=ctx.get("owner"))
