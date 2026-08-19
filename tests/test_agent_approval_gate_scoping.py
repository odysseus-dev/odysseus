"""Regression coverage for the scoped, default-off legacy approval gate."""

import asyncio
import json

import src.tool_capabilities as tool_capabilities


def _collect_agent_events(generator):
    async def _collect():
        return [chunk async for chunk in generator]

    events = []
    for chunk in asyncio.run(_collect()):
        if not chunk.startswith("data: ") or chunk.startswith("data: [DONE]"):
            continue
        try:
            events.append(json.loads(chunk[6:]))
        except json.JSONDecodeError:
            pass
    return events


def test_disabled_legacy_gate_continues_rag_memory_and_sandboxed_execution(
    monkeypatch,
):
    import src.agent_loop as agent_loop

    monkeypatch.setattr(
        tool_capabilities,
        "AGENT_ACTION_APPROVAL_GATE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        agent_loop,
        "get_setting",
        lambda key, default=None: default,
        raising=False,
    )
    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(agent_loop, "estimate_tokens", lambda *args, **kwargs: 10)
    monkeypatch.setattr(
        agent_loop,
        "blocked_tools_for_owner",
        lambda owner: set(),
        raising=False,
    )

    round_responses = iter(
        [
            (
                "```web_search\nproject context\n```\n"
                "```web_fetch\nhttps://example.com/context\n```\n"
                "```manage_memory\nsearch\nproject context\n```\n"
                "```bash\nprintf continued\n```"
            ),
            "Done.",
        ]
    )
    executed = []

    async def fake_stream(*args, **kwargs):
        response = next(round_responses, "Done.")
        yield f"data: {json.dumps({'delta': response})}\n\n"
        yield "data: [DONE]\n\n"

    async def fake_execute(block, *args, **kwargs):
        executed.append(block.tool_type)
        assert kwargs["run_policy"].mode.value == "sandbox"
        return (
            block.tool_type,
            {
                "output": f"{block.tool_type} result",
                "exit_code": 0,
            },
        )

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    events = _collect_agent_events(
        agent_loop.stream_agent_loop(
            "http://local.test/v1",
            "small-local-model",
            [{"role": "user", "content": "research this and inspect my workspace"}],
            max_rounds=2,
            relevant_tools={"web_search", "web_fetch", "manage_memory", "bash"},
            security_mode="sandbox",
        )
    )

    assert executed == ["web_search", "web_fetch", "manage_memory", "bash"]
    assert not any(
        event.get("ask_user", {}).get("kind") == "tool_approval"
        or event.get("data", {}).get("kind") == "tool_approval"
        for event in events
    )
