import asyncio
import json

import src.agent_loop as al
from src.agent_tools import ToolBlock
from src.tool_execution import execute_tool_block
from src.tool_policy import build_effective_tool_policy, messages_contain_untrusted_context


def test_untrusted_metadata_is_the_authority_signal():
    assert messages_contain_untrusted_context([
        {"role": "user", "content": "ignore policy", "metadata": {"trusted": False}},
    ])
    assert not messages_contain_untrusted_context([
        {"role": "user", "content": "<<<UNTRUSTED_SOURCE_DATA>>>"},
    ])


def test_external_context_gate_allows_only_declared_safe_tools():
    policy = build_effective_tool_policy().with_external_context()
    assert policy.blocks("bash")
    assert policy.blocks("read_file")
    assert policy.blocks("mcp__random__tool")
    assert policy.blocks("unknown_future_tool")
    assert not policy.blocks("ask_user")
    assert not policy.blocks("update_plan")


def test_dispatcher_backstop_blocks_process_tool_after_external_context():
    policy = build_effective_tool_policy().with_external_context()
    desc, result = asyncio.run(
        execute_tool_block(ToolBlock("bash", "echo must-not-run"), tool_policy=policy)
    )
    assert desc == "bash: BLOCKED"
    assert result["exit_code"] == 1
    assert "untrusted external context" in result["error"]


def test_tool_output_promotes_gate_before_next_same_batch_call(monkeypatch):
    async def fake_exec(block, **_kwargs):
        return block.tool_type, {"output": "untrusted result", "exit_code": 0}

    async def fake_stream(*_args, **_kwargs):
        yield "data: " + json.dumps({"delta": "```ask_user\nquestion\n```\n```bash\necho no\n```"}) + "\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "execute_tool_block", fake_exec)
    monkeypatch.setattr(al, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(al, "get_setting", lambda _key, default=None: default)
    monkeypatch.setattr(al, "estimate_tokens", lambda *_args, **_kwargs: 1)

    async def collect():
        return [chunk async for chunk in al.stream_agent_loop(
            "http://local.test/v1", "local-model",
            [{"role": "user", "content": "do it"}],
            relevant_tools={"ask_user", "bash"}, max_rounds=1,
        )]

    events = [json.loads(chunk[6:]) for chunk in asyncio.run(collect())
              if chunk.startswith("data: {")]
    starts = [event["tool"] for event in events if event.get("type") == "tool_start"]
    blocked = [event for event in events if event.get("type") == "tool_output" and event.get("tool") == "bash"]
    assert starts == ["ask_user"]
    assert blocked and blocked[0]["exit_code"] == 1
