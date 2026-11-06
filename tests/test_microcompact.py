"""Old tool results are stubbed before an LLM compaction summary."""
from src.context_compactor import microcompact_tool_results

STUB = "[tool result cleared for context"


def _tool(text: str) -> dict:
    return {"role": "tool", "content": text}


def test_keeps_the_last_five_tool_results():
    messages = [{"role": "user", "content": "start"}]
    for i in range(8):
        messages.append(_tool("x" * 400 + str(i)))
    out = microcompact_tool_results(messages, keep_recent_tools=5)
    tools = [m for m in out if m["role"] == "tool"]
    assert len(tools) == 8
    assert all(str(m["content"]).startswith(STUB) for m in tools[:3])
    assert all(not str(m["content"]).startswith(STUB) for m in tools[3:])
    assert out[0]["content"] == "start"
