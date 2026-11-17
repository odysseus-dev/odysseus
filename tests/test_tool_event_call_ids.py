from src.agent_loop import _resolved_tool_call_id


def test_preserves_native_model_tool_call_id():
    assert _resolved_tool_call_id(
        {"id": "chatcmpl-tool-exact"},
        session_id="session-a",
        round_num=2,
        tool_index=1,
        tool_name="write_file",
    ) == "chatcmpl-tool-exact"


def test_generates_stable_id_for_harness_follow_through_call():
    kwargs = {
        "session_id": "session-a",
        "round_num": 2,
        "tool_index": 1,
        "tool_name": "private_browser",
    }

    first = _resolved_tool_call_id({}, **kwargs)
    second = _resolved_tool_call_id(None, **kwargs)

    assert first == second
    assert first.startswith("odysseus-auto-")


def test_generated_ids_distinguish_tool_positions():
    first = _resolved_tool_call_id(
        None,
        session_id="session-a",
        round_num=2,
        tool_index=1,
        tool_name="inspect_media",
    )
    second = _resolved_tool_call_id(
        None,
        session_id="session-a",
        round_num=2,
        tool_index=2,
        tool_name="inspect_media",
    )

    assert first != second
