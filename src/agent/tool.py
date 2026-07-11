"""Tool.define() framework — typed tool definitions with Pydantic validation."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Type, TypeVar

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

P = TypeVar("P", bound=BaseModel)


@dataclass
class ToolResult:
    """Structured output from a tool execution."""
    output: str
    title: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    attachments: Optional[list] = None


class RecoverableError(Exception):
    """Error that the model can fix by retrying with different args."""
    pass


@dataclass
class ToolContext:
    """Context passed to every tool's execute function."""
    session_id: str = ""
    owner: str = ""
    workspace: str = ""
    progress_cb: Optional[Callable] = None
    abort: Optional[Any] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolInfo:
    """Immutable tool definition — created by Tool.define()."""
    id: str
    description: str
    parameters: Type[BaseModel]
    _execute: Callable
    _schema: Optional[Dict] = field(default=None, repr=False)

    async def execute(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            params = self.parameters.model_validate(args)
        except ValidationError as e:
            raise RecoverableError(f"Invalid arguments for {self.id} tool:\n{e}") from e
        return await self._execute(params, ctx)

    def to_schema(self) -> Dict:
        if self._schema:
            return self._schema
        schema = _pydantic_to_json_schema(self.parameters)
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": schema,
            }
        }


class Tool:
    """Factory for creating tool definitions."""

    @staticmethod
    def define(
        id: str,
        description: str,
        parameters: Type[P],
        execute: Callable[[P, ToolContext], Awaitable[ToolResult]],
    ) -> ToolInfo:
        return ToolInfo(id=id, description=description, parameters=parameters, _execute=execute)

    @staticmethod
    def from_legacy(
        id: str,
        description: str,
        handler: Callable[[str, Dict], Awaitable[Dict]],
    ) -> ToolInfo:
        class _PassthroughParams(BaseModel):
            content: str = Field(description="Raw content string")

        async def _adapter(params: _PassthroughParams, ctx: ToolContext) -> ToolResult:
            result = await handler(params.content, {
                "session_id": ctx.session_id,
                "owner": ctx.owner,
                "workspace": ctx.workspace,
                "progress_cb": ctx.progress_cb,
                **ctx.extra,
            })
            if isinstance(result, tuple):
                desc, result = result
            return ToolResult(
                output=result.get("output") or result.get("error") or str(result),
                title=desc if isinstance(result, tuple) else f"{id} result",
                metadata={k: v for k, v in result.items() if k not in ("output", "error")},
            )

        return ToolInfo(id=id, description=description, parameters=_PassthroughParams, _execute=_adapter)


def _pydantic_to_json_schema(model: Type[BaseModel]) -> Dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("$schema", None)
    if "properties" in schema:
        for prop in schema["properties"].values():
            prop.pop("title", None)
            prop.pop("default", None)
    return schema
