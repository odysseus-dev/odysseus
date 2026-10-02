"""system_tools.py — Handler classes for system/utility tools.

Migrates manage_tasks, manage_skills, and api_call out of the
execute_tool_block if/elif chain into the TOOL_HANDLERS registry.
Part of the incremental refactor tracked in issue #2917.
"""
from typing import Dict

from src.tools.system import do_manage_tasks, do_manage_skills, do_api_call


class ManageTasksTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await do_manage_tasks(content, owner=ctx.get("owner"))


class ManageSkillsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await do_manage_skills(content, owner=ctx.get("owner"))


class ApiCallTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await do_api_call(content)
