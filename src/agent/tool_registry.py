"""Tool registry — registration, resolution, filtering, legacy adapter."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List, Optional, Set

from src.agent.tool import Tool, ToolInfo, ToolContext, ToolResult
from src.agent.permission import (
    Action, Rule, Ruleset, evaluate, disabled_tools,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    _tools: Dict[str, ToolInfo]

    def __init__(self) -> None:
        self._tools = {}

    def register(self, tool: ToolInfo) -> None:
        if tool.id in self._tools:
            logger.debug(f"Tool {tool.id} already registered, overwriting")
        self._tools[tool.id] = tool

    def register_legacy(
        self, id: str, description: str,
        handler: Callable[[str, Dict], Awaitable[Dict]],
    ) -> None:
        tool = Tool.from_legacy(id, description, handler)
        self.register(tool)

    def get(self, id: str) -> Optional[ToolInfo]:
        return self._tools.get(id)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def resolve(
        self, ruleset: Optional[Ruleset] = None,
        allowlist: Optional[Set[str]] = None,
    ) -> List[ToolInfo]:
        tools = list(self._tools.values())
        if allowlist is not None:
            tools = [t for t in tools if t.id in allowlist]
        if ruleset is not None:
            dis = disabled_tools([t.id for t in tools], ruleset)
            tools = [t for t in tools if t.id not in dis]
        return tools

    def disabled(self, ruleset: Ruleset) -> Set[str]:
        return disabled_tools(list(self._tools.keys()), ruleset)

    def to_schemas(self, tool_ids: Optional[Set[str]] = None) -> List[Dict]:
        tools = self._tools.values()
        if tool_ids is not None:
            tools = [t for t in tools if t.id in tool_ids]
        return [t.to_schema() for t in tools]
