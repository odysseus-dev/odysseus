"""
tool_registry.py -- Single source of truth for tool definitions.
"""

from __future__ import annotations

import functools
import inspect
import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """One tool definition."""
    name: str
    description: str
    category: str
    params_model: type[BaseModel]
    handler: Callable[..., Any]
    is_async: bool = False
    tags: set[str] = field(default_factory=set)
    always_available: bool = False
    admin_only: bool = False
    deprecated: bool = False

    def to_openai_schema(self) -> dict[str, Any]:
        """OpenAI schema."""
        schema = self.params_model.model_json_schema()
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_prompt_section(self) -> str:
        """Return TOOL_SECTIONS-style usage."""
        fields = self.params_model.model_fields
        if not fields:
            return "- " + chr(96)*3 + self.name + chr(96)*3 + " - " + self.description
        required = {k: v for k, v in fields.items() if v.is_required()}
        if len(required) == 1 and len(fields) <= 3:
            key = next(iter(required.keys()))
            parts = [chr(96)*3 + self.name, "<" + key + ">", chr(96)*3, self.description]
            return chr(10).join(parts)
        lines_p = ["- " + chr(96)*3 + self.name + chr(96)*3 + " - " + self.description]
        lines_p.append("  Parameters (JSON):")
        for fn, info in fields.items():
            desc = info.description or fn.replace("_", " ")
            req = "required" if info.is_required() else "optional"
            lines_p.append("    - " + chr(96) + fn + chr(96) + " (" + req + "): " + desc)
        return chr(10).join(lines_p)


_TOOLS: dict[str, ToolDef] = {}

def get_registry() -> dict[str, ToolDef]:
    return dict(_TOOLS)

def lookup(name: str) -> ToolDef | None:
    return _TOOLS.get(name)

def get_handlers() -> dict[str, Callable]:
    return {n: t.handler for n, t in _TOOLS.items()}

def get_openai_schemas() -> list[dict[str, Any]]:
    return [t.to_openai_schema() for t in _TOOLS.values()]

def get_prompt_sections() -> dict[str, str]:
    return {n: t.to_prompt_section() for n, t in _TOOLS.items()}

def get_tool_names() -> set[str]:
    return set(_TOOLS.keys())

def get_always_available() -> set[str]:
    return {n for n, t in _TOOLS.items() if t.always_available}

def get_admin_tools() -> set[str]:
    return {n for n, t in _TOOLS.items() if t.admin_only}


def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    category: str = "general",
    tags: set[str] | None = None,
    always_available: bool = False,
    admin_only: bool = False,
    deprecated: bool = False,
):
    """Decorator that registers a Pydantic-parameter function as a tool."""
    def decorator(func: Callable) -> Callable:
        nonlocal name, description, category, tags
        resolved_name = name or func.__name__.replace("_", "-")
        if description is None:
            doc = func.__doc__ or ""
            description = textwrap.dedent(doc).strip() or resolved_name.replace("-", " ").title()
        if tags is None:
            tags = set()
        hints = inspect.get_annotations(func)
        sig = inspect.signature(func)
        params_model: type[BaseModel] | None = None
        for p in sig.parameters.values():
            ann = hints.get(p.name)
            if ann is not None and isinstance(ann, type) and issubclass(ann, BaseModel):
                params_model = ann
                break
        if params_model is None:
            raise TypeError(
                "@" + resolved_name + ": handler must have a Pydantic BaseModel parameter. "
                + "Signature: " + str(sig)
            )
        is_async = inspect.iscoroutinefunction(func)
        td = ToolDef(
            name=resolved_name,
            description=description,
            category=category,
            params_model=params_model,
            handler=func,
            is_async=is_async,
            tags=tags,
            always_available=always_available,
            admin_only=admin_only,
            deprecated=deprecated,
        )
        if resolved_name in _TOOLS:
            logger.warning("Tool %r already registered -- overwriting", resolved_name)
        _TOOLS[resolved_name] = td

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator


def discover_tools() -> None:
    """Import src.agent_tools.* so @tool decorators run."""
    import importlib, pkgutil, os as _os
    try:
        pkg = importlib.import_module("src.agent_tools")
        pkg_path = _os.path.dirname(pkg.__file__)
        for _importer, modname, _ispkg in pkgutil.iter_modules([pkg_path]):
            if modname.startswith("_"):
                continue
            try:
                importlib.import_module("src.agent_tools." + modname)
            except Exception as exc:
                logger.debug("Skipping %s: %s", modname, exc)
    except Exception as exc:
        logger.debug("Auto-discovery not available: %s", exc)


_discovered = False
if not _discovered:
    discover_tools()
    _discovered = True
