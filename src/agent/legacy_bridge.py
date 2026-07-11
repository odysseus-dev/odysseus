"""Bridge between new Tool.define() framework and existing TOOL_HANDLERS."""
from __future__ import annotations

import logging
from typing import Dict, List, Set

from src.agent.tool import Tool, ToolInfo
from src.agent.tool_registry import ToolRegistry
from src.agent.permission import AGENT_PERMISSIONS

logger = logging.getLogger(__name__)


def create_registry_from_legacy() -> ToolRegistry:
    """Create a ToolRegistry populated with existing TOOL_HANDLERS."""
    from src.agent_tools import TOOL_HANDLERS
    registry = ToolRegistry()
    for name, handler in TOOL_HANDLERS.items():
        try:
            registry.register_legacy(name, f"Legacy tool: {name}", handler)
        except Exception as e:
            logger.warning(f"Failed to register legacy tool {name}: {e}")
    return registry


def get_tool_schemas_for_agent(
    agent: str = "build",
    disabled: Set[str] = None,
    relevant_tools: Set[str] = None,
) -> List[Dict]:
    """Get tool schemas filtered by agent permissions and relevance."""
    registry = create_registry_from_legacy()
    ruleset = list(AGENT_PERMISSIONS.get(agent, []))
    if disabled:
        from src.agent.permission import Rule, Action
        for tool_id in disabled:
            ruleset.append(Rule(permission=tool_id, pattern="*", action=Action.DENY))
    tools = registry.resolve(ruleset=ruleset, allowlist=relevant_tools)
    return registry.to_schemas(tool_ids={t.id for t in tools})
