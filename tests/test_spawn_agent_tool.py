# tests/test_spawn_agent_tool.py
"""Multiagent slice-1 Task 5: spawn_agent tool wiring end-to-end (mocked LLM)."""
import asyncio
import json

import pytest

from src import subagent_orchestrator as orch
from src.agent_tools import TOOL_TAGS, ToolBlock
from src.tool_execution import execute_tool_block


def test_spawn_agent_registered_everywhere():
    assert "spawn_agent" in TOOL_TAGS
    from src.agent_loop import TOOL_SECTIONS
    assert "spawn_agent" in TOOL_SECTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    names = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert "spawn_agent" in names


def test_function_call_maps_to_tool_block():
    from src.tool_schemas import function_call_to_tool_block
    block = function_call_to_tool_block(
        "spawn_agent",
        json.dumps({"agents": [{"agent": "a", "task": "t"}]}))
    assert block is not None and block.tool_type == "spawn_agent"
    assert json.loads(block.content)["agents"][0]["task"] == "t"


@pytest.fixture()
def profiles_in_data(tmp_path, monkeypatch):
    from services.business_platform.profile_compiler import (
        GENERAL_OFFICE_CATALOG_PATH, SEED_SKILLS_DIR, compile_profile,
        load_catalog,
    )
    cat = load_catalog(GENERAL_OFFICE_CATALOG_PATH, skills_dir=SEED_SKILLS_DIR)
    compile_profile(cat, tmp_path)
    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    return tmp_path


def test_execute_spawn_agent_end_to_end(profiles_in_data, monkeypatch):
    seen = {}

    async def fake_loop(*, binding, messages, endpoint_url, model, headers,
                        tools, disable_spawn, session_id):
        seen.update(binding=binding, endpoint=endpoint_url, model=model,
                    tools=set(tools), disable_spawn=disable_spawn)
        return "subagent says hi"

    monkeypatch.setattr(orch, "_default_run_loop", fake_loop)

    async def run():
        token = orch.seed_run_context(
            endpoint_url="http://coord", model="coord-model", headers=None,
            owner="oleg", session_id="coord-session")
        try:
            return await execute_tool_block(
                ToolBlock("spawn_agent", json.dumps({
                    "agents": [{"agent": "general_office-seo",
                                "task": "audit the site"}]})),
                session_id="coord-session", owner="oleg")
        finally:
            orch.reset_run_context(token)

    desc, result = asyncio.run(run())
    assert "spawn_agent: 1 agent(s)" in desc
    assert result["status"] == "ok"
    assert result["results"][0]["output"] == "subagent says hi"
    assert result["results"][0]["owner"] == "agent:oleg/general_office-seo"
    assert seen["endpoint"] == "http://coord" and seen["model"] == "coord-model"


def test_execute_spawn_agent_malformed_json(profiles_in_data):
    async def run():
        return await execute_tool_block(
            ToolBlock("spawn_agent", "{not json"),
            session_id="s", owner="oleg")

    desc, result = asyncio.run(run())
    assert result["status"] == "error" and "JSON" in result["error"]


def test_seed_run_context_is_authoritative_no_stale_bleed():
    """Top-level seed must NOT inherit a leftover context: a stale human/
    depth from a prior loop in the same async context must never carry into
    a new request (cross-user identity bleed)."""
    t1 = orch.seed_run_context(endpoint_url="e", model="m", headers=None,
                               owner="oleg", session_id="s1")
    ctx1 = orch.get_run_context()
    assert ctx1["human_owner"] == "oleg" and ctx1["depth"] == 0
    # token of the first seed deliberately NOT reset (simulates the loop that
    # ignored its token). A new top-level seed for a DIFFERENT user must fully
    # overwrite — not preserve oleg.
    orch.seed_run_context(endpoint_url="e", model="m", headers=None,
                          owner="bob", session_id="s2")
    ctx2 = orch.get_run_context()
    assert ctx2["human_owner"] == "bob" and ctx2["depth"] == 0
    orch.reset_run_context(t1)
