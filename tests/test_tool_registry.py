"""Tests for src/agent/tool_registry.py"""
from __future__ import annotations
import pytest
from pydantic import BaseModel, Field
from src.agent.tool import Tool, ToolResult, ToolContext
from src.agent.permission import Action, Rule, Ruleset
from src.agent.tool_registry import ToolRegistry


class EchoParams(BaseModel):
    message: str = Field(description="Message to echo")


async def _echo(params: EchoParams, ctx: ToolContext) -> ToolResult:
    return ToolResult(output=params.message, title="Echo")


EchoTool = Tool.define("echo", "Echoes message", EchoParams, _echo)


def test_registry_register():
    reg = ToolRegistry()
    reg.register(EchoTool)
    assert "echo" in reg.list_tools()


def test_registry_register_duplicate():
    reg = ToolRegistry()
    reg.register(EchoTool)
    reg.register(EchoTool)
    assert len(reg.list_tools()) == 1


def test_registry_get():
    reg = ToolRegistry()
    reg.register(EchoTool)
    tool = reg.get("echo")
    assert tool is EchoTool


def test_registry_get_unknown():
    reg = ToolRegistry()
    assert reg.get("nonexistent") is None


def test_registry_resolve_all_allowed():
    reg = ToolRegistry()
    reg.register(EchoTool)
    tools = reg.resolve()
    assert len(tools) == 1


def test_registry_resolve_with_deny():
    reg = ToolRegistry()
    reg.register(EchoTool)
    rules = [Rule(permission="echo", pattern="*", action=Action.DENY)]
    tools = reg.resolve(ruleset=rules)
    assert len(tools) == 0


def test_registry_resolve_with_allowlist():
    reg = ToolRegistry()
    reg.register(EchoTool)
    class FooParams(BaseModel):
        x: int = Field(default=1)
    async def _foo(p: FooParams, c: ToolContext) -> ToolResult:
        return ToolResult(output="foo", title="Foo")
    FooTool = Tool.define("foo", "Foo tool", FooParams, _foo)
    reg.register(FooTool)
    tools = reg.resolve(allowlist={"echo"})
    assert len(tools) == 1
    assert tools[0].id == "echo"


def test_registry_disabled():
    reg = ToolRegistry()
    reg.register(EchoTool)
    rules = [Rule(permission="echo", pattern="*", action=Action.DENY)]
    disabled = reg.disabled(rules)
    assert "echo" in disabled


def test_registry_to_schemas():
    reg = ToolRegistry()
    reg.register(EchoTool)
    schemas = reg.to_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"


def test_registry_from_legacy():
    reg = ToolRegistry()
    async def old_handler(content: str, ctx: dict) -> dict:
        return {"output": f"old: {content}", "exit_code": 0}
    reg.register_legacy("old_tool", "Old tool", old_handler)
    assert "old_tool" in reg.list_tools()
    tool = reg.get("old_tool")
    assert tool.id == "old_tool"
