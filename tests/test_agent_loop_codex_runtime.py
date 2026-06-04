import json

import pytest

from src import agent_loop


@pytest.mark.asyncio
async def test_codex_runtime_agent_mode_does_not_send_native_tool_schemas(monkeypatch):
    captured = {}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(
        agent_loop,
        "_build_system_prompt",
        lambda messages, *args, **kwargs: (
            [{"role": "system", "content": "agent prompt"}] + list(messages),
            [],
        ),
    )

    def fake_get_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 0
        if key == "agent_stream_timeout_seconds":
            return 5
        if key == "agent_verifier_subagent":
            return False
        return default

    monkeypatch.setattr(agent_loop, "get_setting", fake_get_setting)
    monkeypatch.setattr(
        agent_loop,
        "FUNCTION_TOOL_SCHEMAS",
        [{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
    )

    async def fake_stream_llm_with_fallback(candidates, messages, **kwargs):
        captured["candidates"] = candidates
        captured["messages"] = messages
        captured["tools"] = kwargs.get("tools")
        yield f"data: {json.dumps({'delta': 'ok'})}\n\n"
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream_llm_with_fallback)

    chunks = [
        chunk
        async for chunk in agent_loop.stream_agent_loop(
            "codex://runtime/chat/completions",
            "gpt-5.5",
            [{"role": "user", "content": "Use a tool if needed"}],
            relevant_tools={"bash"},
            max_rounds=1,
        )
    ]

    assert captured["candidates"][0][0] == "codex://runtime/chat/completions"
    assert captured["tools"] is None
    assert agent_loop._CODEX_RUNTIME_AGENT_PREFACE in captured["messages"][0]["content"]
    assert any('"delta": "ok"' in chunk for chunk in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"
