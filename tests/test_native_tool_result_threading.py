"""Native tool-call results must be threaded by CONVERTED-call position.

When an OpenAI/Anthropic model emits several tool_calls in one round and one
fails to convert (hallucinated name or bad-JSON args), it is dropped from
tool_blocks (so it produces no result) but used to stay in native_tool_calls.
_append_tool_results indexed tool_result_texts by native-call position, so the
surviving result was attached to the wrong tool_call_id and the real call was
answered with an empty string. _resolve_tool_blocks now returns the converted
calls aligned 1:1 with tool_blocks/tool_result_texts, and that aligned list is
what is threaded back.
"""
import src.agent_loop as al


def test_resolve_returns_converted_calls_aligned():
    native = [
        {"name": "bogus_unknown_tool", "arguments": "{}", "id": "A"},
        {"name": "web_search", "arguments": '{"query": "hello"}', "id": "B"},
    ]
    tool_blocks, used_native, converted = al._resolve_tool_blocks("", native, 1)
    assert used_native is True
    assert len(tool_blocks) == 1           # only web_search converted
    assert [c["name"] for c in converted] == ["web_search"]
    assert len(converted) == len(tool_blocks)  # aligned 1:1


def test_append_threads_result_to_correct_tool_call_id():
    messages = []
    converted = [{"id": "B", "name": "web_search", "arguments": "{}"}]
    al._append_tool_results(
        messages, "some response", converted,
        ["RESULT"], ["RESULT"], True, 1,
    )
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "B"
    assert tool_msgs[0]["content"] == "RESULT"
    asst = next(m for m in messages if m.get("role") == "assistant")
    assert [tc["id"] for tc in asst["tool_calls"]] == ["B"]
