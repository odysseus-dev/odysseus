"""Builtin OpenAI function-tool schema provider."""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from src.capabilities.models import ToolContext, ToolDefinition
from src.capabilities.registry import CapabilityRegistry


class BuiltinToolProvider:
    provider_id = "builtin"

    def __init__(
        self,
        schemas: Sequence[dict],
        *,
        admin_schema_names: Iterable[str] = (),
    ) -> None:
        self._schemas = schemas
        self._admin_schema_names = frozenset(admin_schema_names)

    def list_tools(self, context: ToolContext | None = None) -> Iterable[ToolDefinition]:
        for schema in self._schemas:
            name = _schema_name(schema)
            if not name:
                raise ValueError("Builtin tool schema is missing function.name")
            yield ToolDefinition(
                name=name,
                schema=schema,
                provider_id=self.provider_id,
                admin_only=name in self._admin_schema_names,
            )


def _schema_name(schema: Mapping) -> str:
    function = schema.get("function", {}) if isinstance(schema, Mapping) else {}
    return str(function.get("name", "") or "")


def get_builtin_function_schemas(
    schemas: Sequence[dict],
    *,
    admin_schema_names: Iterable[str] = (),
) -> list[dict]:
    registry = CapabilityRegistry()
    registry.register_provider(
        BuiltinToolProvider(schemas, admin_schema_names=admin_schema_names)
    )
    return registry.list_schemas()
