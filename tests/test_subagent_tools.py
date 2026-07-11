"""Tests for src/agent_tools/subagent_tools.py"""
from __future__ import annotations
import pytest
from src.agent_tools.subagent_tools import (
    SpawnSubagentTool, WaitActorTool, ListActorsTool,
)


@pytest.mark.asyncio
async def test_spawn_subagent_tool_execute():
    tool = SpawnSubagentTool()
    result = await tool.execute(
        '{"task": "Find all Python files", "agent_type": "explore"}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert "actor_id" in result
    assert result["status"] == "spawned"


@pytest.mark.asyncio
async def test_spawn_subagent_tool_background():
    tool = SpawnSubagentTool()
    result = await tool.execute(
        '{"task": "Run tests", "agent_type": "general", "background": true}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert result["background"] is True


@pytest.mark.asyncio
async def test_wait_actor_tool_execute():
    tool = WaitActorTool()
    result = await tool.execute(
        '{"actor_id": "explore-1", "timeout": 0.1}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert "status" in result


@pytest.mark.asyncio
async def test_list_actors_tool_execute():
    tool = ListActorsTool()
    result = await tool.execute('{}', {"session_id": "test-session", "owner": "test"})
    assert "actors" in result
    assert isinstance(result["actors"], list)


def test_spawn_subagent_tool_schema():
    tool = SpawnSubagentTool()
    schema = tool._get_schema()
    assert schema["name"] == "spawn_subagent"
    assert "task" in schema["parameters"]["properties"]
    assert "agent_type" in schema["parameters"]["properties"]


def test_wait_actor_tool_schema():
    tool = WaitActorTool()
    schema = tool._get_schema()
    assert schema["name"] == "wait_actor"
    assert "actor_id" in schema["parameters"]["properties"]


def test_list_actors_tool_schema():
    tool = ListActorsTool()
    schema = tool._get_schema()
    assert schema["name"] == "list_actors"
