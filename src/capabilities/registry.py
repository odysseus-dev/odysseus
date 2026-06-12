"""Provider-backed capability registry."""

from __future__ import annotations

from typing import Dict, Iterable, List, Protocol

from src.capabilities.models import ToolContext, ToolDefinition


class CapabilityProvider(Protocol):
    provider_id: str

    def list_tools(self, context: ToolContext | None = None) -> Iterable[ToolDefinition]:
        """Return tool definitions exposed by this provider."""


class CapabilityRegistry:
    """Collect tool definitions from providers while preserving order."""

    def __init__(self) -> None:
        self._providers: Dict[str, CapabilityProvider] = {}

    def register_provider(self, provider: CapabilityProvider) -> None:
        provider_id = getattr(provider, "provider_id", "")
        if not provider_id:
            raise ValueError("Capability provider must define provider_id")
        if provider_id in self._providers:
            raise ValueError(f"Capability provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def require_provider(self, provider_id: str) -> CapabilityProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"Unknown capability provider: {provider_id}") from exc

    def list_tools(self, context: ToolContext | None = None) -> List[ToolDefinition]:
        definitions: List[ToolDefinition] = []
        seen: Dict[str, str] = {}

        for provider in self._providers.values():
            for definition in provider.list_tools(context):
                if definition.name in seen:
                    raise ValueError(
                        f"Tool name conflict: {definition.name} from "
                        f"{definition.provider_id} already exposed by {seen[definition.name]}"
                    )
                seen[definition.name] = definition.provider_id
                definitions.append(definition)

        return definitions

    def list_schemas(self, context: ToolContext | None = None) -> List[dict]:
        return [definition.schema for definition in self.list_tools(context)]
