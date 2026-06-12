"""Shared data contracts for capability providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class ToolContext:
    """Request-scoped context used to list or execute capabilities."""

    owner: Optional[str] = None
    needs_admin: bool = False
    disabled_tools: frozenset[str] = field(default_factory=frozenset)
    mcp_disabled_map: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolDefinition:
    """A tool schema plus its provider metadata."""

    name: str
    schema: Mapping[str, Any]
    provider_id: str
    admin_only: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ToolDefinition.name is required")
        if not self.provider_id:
            raise ValueError("ToolDefinition.provider_id is required")


@dataclass(frozen=True)
class ToolRequest:
    """Normalized request for a future executor facade."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    context: ToolContext = field(default_factory=ToolContext)


@dataclass(frozen=True)
class ToolResult:
    """Normalized result for a future executor facade."""

    name: str
    output: Any = None
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None
