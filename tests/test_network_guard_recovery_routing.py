from src.agent_loop import _blocked_network_recovery_tools


def test_network_guard_preserves_native_recovery_tools():
    events = [
        {
            "type": "tool_output",
            "tool": "python",
            "output": (
                "python: ad-hoc HTTP access is disabled when native web tools "
                "are available. Use pdf_extract for online PDFs, web_fetch for "
                "a concrete page, or web_search for discovery."
            ),
        },
    ]

    assert _blocked_network_recovery_tools(events) == {
        "pdf_extract",
        "web_fetch",
        "web_search",
    }


def test_structured_tool_result_preserves_native_recovery_tools():
    events = [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "call-1",
            "content": [{
                "type": "text",
                "text": (
                    "bash: ad-hoc HTTP downloads are disabled when native web "
                    "tools are available. Use pdf_extract for online PDFs."
                ),
            }],
        }],
    }]

    assert _blocked_network_recovery_tools(events) == {
        "pdf_extract",
        "web_fetch",
        "web_search",
    }


def test_persisted_internal_tool_event_preserves_recovery_tools():
    events = [{
        "tool": "bash",
        "output": (
            "bash: ad-hoc HTTP downloads are disabled when native web tools "
            "are available. Use pdf_extract for online PDFs."
        ),
        "exit_code": 1,
    }]

    assert _blocked_network_recovery_tools(events) == {
        "pdf_extract",
        "web_fetch",
        "web_search",
    }


def test_tool_event_state_exists_before_initial_route_build():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    init = source.index("tool_events = []   # Available to initial")
    first_build = source.index("_route_state = await _build_route_request_state(", init)
    assert init < first_build


def test_network_recovery_floor_precedes_round_schema_materialization():
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src" / "agent_loop.py").read_text()
    floor = source.index("_network_recovery_floor = (")
    active_state = source.index("_active_route_state = {", floor)
    schemas = source.index("all_tool_schemas = _tool_schemas_for_route", active_state)
    assert floor < active_state < schemas


def test_non_tool_event_cannot_widen_route():
    events = [{
        "type": "message",
        "role": "user",
        "output": (
            "python: ad-hoc HTTP access is disabled when native web tools are "
            "available. Use pdf_extract for online PDFs."
        ),
    }]

    assert _blocked_network_recovery_tools(events) == set()


def test_unrelated_tool_failure_does_not_widen_route():
    events = [{"type": "tool_output", "tool": "bash", "output": "Permission denied"}]

    assert _blocked_network_recovery_tools(events) == set()
