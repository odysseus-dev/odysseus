"""Tests for src/agent/tool.py"""
from __future__ import annotations
import pytest
from pydantic import BaseModel, Field
from src.agent.tool import Tool, ToolResult, RecoverableError, ToolContext


class EchoParams(BaseModel):
    message: str = Field(description="Message to echo back")
    uppercase: bool = Field(default=False, description="Convert to uppercase")


class FailParams(BaseModel):
    reason: str = Field(description="Reason to fail")


async def _echo_execute(params: EchoParams, ctx: ToolContext) -> ToolResult:
    text = params.message.upper() if params.uppercase else params.message
    return ToolResult(output=text, title=f"Echoed: {text[:30]}")


async def _fail_execute(params: FailParams, ctx: ToolContext) -> ToolResult:
    raise RecoverableError(f"Intentional failure: {params.reason}")


EchoTool = Tool.define(
    "echo",
    description="Echoes a message back",
    parameters=EchoParams,
    execute=_echo_execute,
)

FailTool = Tool.define(
    "fail",
    description="Always fails with a message",
    parameters=FailParams,
    execute=_fail_execute,
)


def test_tool_has_id():
    assert EchoTool.id == "echo"


def test_tool_has_description():
    assert EchoTool.description == "Echoes a message back"


def test_tool_has_parameters():
    assert EchoTool.parameters == EchoParams


def test_tool_result_dataclass():
    result = ToolResult(output="hello", title="Greeting")
    assert result.output == "hello"
    assert result.title == "Greeting"
    assert result.metadata == {}
    assert result.attachments is None


def test_tool_result_with_metadata():
    result = ToolResult(output="ok", title="Done", metadata={"exit_code": 0})
    assert result.metadata == {"exit_code": 0}


@pytest.mark.asyncio
async def test_tool_execute_success():
    ctx = ToolContext(session_id="test", owner="test")
    result = await EchoTool.execute({"message": "hello"}, ctx)
    assert result.output == "hello"
    assert result.title == "Echoed: hello"


@pytest.mark.asyncio
async def test_tool_execute_with_optional():
    ctx = ToolContext(session_id="test", owner="test")
    result = await EchoTool.execute({"message": "hello", "uppercase": True}, ctx)
    assert result.output == "HELLO"


@pytest.mark.asyncio
async def test_tool_execute_validation_error():
    ctx = ToolContext(session_id="test", owner="test")
    with pytest.raises(RecoverableError):
        await EchoTool.execute({}, ctx)


@pytest.mark.asyncio
async def test_tool_execute_recoverable_error():
    ctx = ToolContext(session_id="test", owner="test")
    with pytest.raises(RecoverableError) as exc_info:
        await FailTool.execute({"reason": "test"}, ctx)
    assert "test" in str(exc_info.value)


def test_tool_to_schema():
    schema = EchoTool.to_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"
    assert "message" in schema["function"]["parameters"]["properties"]


def test_legacy_adapter():
    async def old_handler(content: str, ctx: dict) -> dict:
        return {"output": f"old: {content}", "exit_code": 0}

    LegacyTool = Tool.from_legacy("legacy_echo", "Old echo tool", old_handler)
    assert LegacyTool.id == "legacy_echo"
    schema = LegacyTool.to_schema()
    assert schema["function"]["name"] == "legacy_echo"
