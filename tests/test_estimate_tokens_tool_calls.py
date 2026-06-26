"""Issue #2748 — estimate_tokens must count assistant tool_calls (name + arguments).

A tool-only assistant turn is stored with content=None and the real payload (e.g.
a large create_document body) in tool_calls[].function.arguments. Before this fix
estimate_tokens ignored tool_calls, so such a turn counted as ~4 tokens and the
compaction/trim gates that rely on estimate_tokens silently missed real context
overflow, letting the upstream call 400 with 'context length exceeded'.
"""

from src.model_context import estimate_tokens


def test_tool_call_arguments_are_counted():
    big = "x" * 40000  # ~ a large create_document body
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "create_document", "arguments": big}},
        ],
    }
    est = estimate_tokens([msg])
    # ~40k chars * 0.3 ≈ 12000, vs the old ~4 that ignored tool_calls entirely.
    assert est > 10000, est


def test_content_only_message_is_unchanged():
    # No tool_calls -> identical to the previous behaviour (content*0.3 + overhead).
    msg = {"role": "user", "content": "x" * 100}
    assert estimate_tokens([msg]) == 4 + int(100 * 0.3)


def test_dict_arguments_are_handled():
    # Some shapes store arguments as a dict rather than a JSON string.
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"function": {"name": "f", "arguments": {"path": "x" * 1000}}}],
    }
    assert estimate_tokens([msg]) > 200


def test_empty_and_malformed_tool_calls_are_safe():
    # tool_calls=None and non-dict entries must not raise and must not inflate.
    assert estimate_tokens([{"role": "assistant", "content": "hi", "tool_calls": None}]) == 4 + int(2 * 0.3)
    assert estimate_tokens([{"role": "assistant", "content": None, "tool_calls": ["bad", 5]}]) == 4
