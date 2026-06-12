"""MCP OpenAI function-tool schema provider."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Set

from src.capabilities.models import ToolContext, ToolDefinition
from src.capabilities.registry import CapabilityRegistry


class McpToolProvider:
    provider_id = "mcp"

    def __init__(
        self,
        mcp_manager,
        *,
        disabled_map: Optional[Dict[str, Set[str]]] = None,
    ) -> None:
        self._mcp_manager = mcp_manager
        self._disabled_map = disabled_map

    def list_tools(self, context: ToolContext | None = None) -> Iterable[ToolDefinition]:
        disabled_map = self._disabled_map
        if disabled_map is None and context is not None:
            disabled_map = {
                server_id: set(names)
                for server_id, names in context.mcp_disabled_map.items()
            }

        for schema in self._mcp_manager.get_all_openai_schemas(disabled_map or {}):
            name = _schema_name(schema)
            if not name:
                raise ValueError("MCP tool schema is missing function.name")
            yield ToolDefinition(
                name=name,
                schema=schema,
                provider_id=self.provider_id,
                admin_only=True,
            )


def _schema_name(schema: Mapping) -> str:
    function = schema.get("function", {}) if isinstance(schema, Mapping) else {}
    return str(function.get("name", "") or "")


def get_mcp_function_schemas(
    mcp_manager,
    *,
    disabled_map: Optional[Dict[str, Set[str]]] = None,
) -> list[dict]:
    registry = CapabilityRegistry()
    registry.register_provider(McpToolProvider(mcp_manager, disabled_map=disabled_map))
    return registry.list_schemas()
