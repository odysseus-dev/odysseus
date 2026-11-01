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


def test_email_backend_error_is_not_rendered_as_empty_inbox():
    raw = (
        "[EMAIL ACCOUNT ERRORS: Primary: [Errno 111] Connection refused]\n"
        "No unread/unresponded emails found."
    )

    summary = al._email_list_summary_from_tool_output(raw)

    assert "currently unavailable" in summary
    assert "No emails found" not in summary


def test_private_browser_product_query_extracts_short_storefront_term():
    assert al._private_browser_product_query(
        "Browse IKEA and find the best chair."
    ) == "chair"
    assert al._private_browser_product_query(
        "Search for a standing desk on IKEA"
    ) == "a standing desk"
    assert al._private_browser_product_query("Where is IKEA?") == ""


def test_unrequested_browser_placeholder_is_rejected():
    block = al.ToolBlock(
        "private_browser",
        '{"action":"batch","commands":[["open","https://www.example.com"],["snapshot"]]}',
    )
    assert al._private_browser_uses_unrequested_placeholder(
        block, "Open the best IKEA chair option"
    )
    assert not al._private_browser_uses_unrequested_placeholder(
        block, "Open https://www.example.com"
    )


def test_browser_placeholder_named_by_prior_user_turn_remains_allowed():
    block = al.ToolBlock(
        "private_browser",
        '{"action":"batch","commands":[["open","https://example.com"],["snapshot"]]}',
    )
    history = [
        {"role": "user", "content": "Open https://example.com and report its heading."},
        {"role": "assistant", "content": "The heading is Example Domain."},
        {"role": "user", "content": "Return to that browser page and open Learn more."},
    ]
    assert not al._private_browser_uses_unrequested_placeholder(
        block, history[-1]["content"], history,
    )


def test_ordinal_task_mutation_binds_to_prior_list_order():
    first = "11111111-1111-4111-8111-111111111111"
    second = "22222222-2222-4222-8222-222222222222"
    history = [{
        "role": "assistant",
        "content": "Found two tasks.",
        "metadata": {"tool_events": [{
            "tool": "manage_tasks", "command": '{"action":"list"}', "exit_code": 0,
            "output": f"AI: Found 2 tasks:\n1. Beta ({first}) — active\n2. Alpha ({second}) — active",
        }]},
    }]
    assert al._ordinal_collection_mutation_target(
        "Delete the second task from that list.", history, None, "tasks",
    ) == second


def test_ordinal_calendar_mutation_binds_to_prior_list_order():
    first = "33333333-3333-4333-8333-333333333333"
    second = "44444444-4444-4444-8444-444444444444"
    history = [{
        "role": "assistant",
        "content": "Two calendar events.",
        "metadata": {"tool_events": [{
            "tool": "manage_calendar", "command": '{"action":"list_events"}', "exit_code": 0,
            "output": f"- [Alpha](#event-{first})\n- [Beta](#event-{second})",
        }]},
    }]
    assert al._ordinal_collection_mutation_target(
        "Delete the second event from that list.", history, None, "calendar",
    ) == second


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
