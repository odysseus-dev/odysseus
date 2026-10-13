from src.agent_loop import (
    _tool_routing_audit_payload,
    _unoffered_web_search_should_synthesize,
)


def test_tool_routing_audit_preserves_each_stage_and_gap():
    payload = _tool_routing_audit_payload(
        round_num=2,
        retrieved_tools={"read_file", "write_file", "private_browser"},
        selected_tools={"read_file", "write_file", "private_browser"},
        declared_tools=set(),
        excluded_tools={"update_plan"},
        offered_tools=["write_file", "read_file"],
        prompt_tokens=1234,
        transport="native_schema",
        system_prompt_chars=2400,
        tool_schema_chars=3600,
    )
    assert payload["retrieved_tools"] == ["private_browser", "read_file", "write_file"]
    assert payload["selected_tools"] == ["private_browser", "read_file", "write_file"]
    assert payload["offered_tools"] == ["read_file", "write_file"]
    assert payload["intentionally_excluded_tools"] == ["update_plan"]
    assert payload["selected_not_offered"] == ["private_browser"]
    assert payload["prompt_tokens_estimate"] == 1234
    assert payload["transport"] == "native_schema"
    assert payload["system_prompt_chars"] == 2400
    assert payload["tool_schema_chars"] == 3600


def test_tool_routing_audit_distinguishes_unknown_retrieval_from_empty():
    payload = _tool_routing_audit_payload(
        round_num=1,
        retrieved_tools=None,
        selected_tools=set(),
        declared_tools=set(),
        offered_tools=[],
    )
    assert payload["retrieved_tools"] is None
    assert payload["selected_tools"] == []
    assert payload["selected_not_offered"] == []
    assert payload["prompt_tokens_estimate"] is None
    assert payload["system_prompt_chars"] is None
    assert payload["tool_schema_chars"] is None
    assert payload["transport"] == "unknown"


def test_tool_routing_audit_does_not_report_policy_exclusions_as_gaps():
    payload = _tool_routing_audit_payload(
        round_num=1,
        retrieved_tools={"read_file", "update_plan"},
        selected_tools={"read_file", "update_plan"},
        offered_tools=["read_file"],
        excluded_tools={"update_plan"},
    )

    assert payload["selected_not_offered"] == []


def test_tool_routing_audit_marks_intentionally_tool_free_final_round():
    payload = _tool_routing_audit_payload(
        round_num=10,
        retrieved_tools={"inspect_media", "read_file"},
        selected_tools={"inspect_media", "read_file"},
        offered_tools=[],
        transport="none",
        offering_suppressed_reason="forced_final_answer",
    )

    assert payload["selected_tools"] == ["inspect_media", "read_file"]
    assert payload["offered_tools"] == []
    assert payload["selected_not_offered"] == []
    assert payload["offering_suppressed_reason"] == "forced_final_answer"


def test_tool_routing_audit_does_not_report_schema_gaps_for_textual_transport():
    payload = _tool_routing_audit_payload(
        round_num=2,
        retrieved_tools={"bash", "python", "write_file"},
        selected_tools={"bash", "python", "write_file"},
        offered_tools=[],
        transport="textual",
        offering_suppressed_reason="textual_tool_transport",
    )

    assert payload["selected_not_offered"] == []
    assert payload["offering_suppressed_reason"] == "textual_tool_transport"


def test_local_media_artifact_compaction_identifies_web_as_intentional_exclusion():
    from src import agent_loop as al

    selected = {
        "inspect_media", "private_browser", "read_file", "write_file",
        "web_search", "web_fetch",
    }
    allowed = al._compact_native_artifact_tools(
        selected,
        text=(
            "Inspect /workspace/fixtures/source.png and create "
            "/workspace/output.html"
        ),
        artifacts=["/workspace/output.html"],
        media_inputs=["/workspace/fixtures/source.png"],
    )
    payload = _tool_routing_audit_payload(
        round_num=1,
        retrieved_tools=selected,
        selected_tools=selected,
        offered_tools=sorted(allowed),
        excluded_tools=selected - allowed,
    )

    assert payload["selected_not_offered"] == []
    assert {"web_search", "web_fetch"} <= set(
        payload["intentionally_excluded_tools"]
    )


def test_unoffered_search_after_successful_fetch_requires_synthesis():
    fetched = [{
        "tool": "web_fetch",
        "exit_code": 0,
        "output": "Authoritative evidence",
    }]

    assert _unoffered_web_search_should_synthesize(
        ["web_search"],
        accepted_tools=[],
        tool_events=fetched,
    )
    assert not _unoffered_web_search_should_synthesize(
        ["web_search"],
        accepted_tools=["web_fetch"],
        tool_events=fetched,
    )
    assert not _unoffered_web_search_should_synthesize(
        ["web_search"],
        accepted_tools=[],
        tool_events=[{"tool": "web_fetch", "exit_code": 1, "output": "failed"}],
    )
