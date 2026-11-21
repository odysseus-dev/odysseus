"""Regression tests for issue #6184: Chat Calendar data reads vs. panel navigation."""

import pytest

from src.action_intents import classify_tool_intent
from src.agent_loop import _parse_explicit_open_panel_request
from src.tool_policy import ToolPolicy
from src.turn_contract import (
    requested_capabilities,
    required_read_operation_for_request,
    resolve_turn_contract,
)


@pytest.mark.parametrize(
    "prompt",
    [
        "What is on my calendar?",
        "What's on my calendar?",
        "List my calendar events",
        "Show all my calendar events",
        "What events are in my calendar?",
        "What events are on my calendar?",
        "Show my calendar",
        "Check my calendar",
        "Check my schedule",
        "Read my calendar events",
        "Do I have any events in my calendar?",
        "List my calendars",
    ],
)
def test_calendar_data_read_phrasings_route_to_calendar(prompt):
    intent = classify_tool_intent(prompt)
    assert intent.needs_tools is True
    assert intent.category == "calendar"
    assert requested_capabilities(prompt) == {"calendar"}
    assert _parse_explicit_open_panel_request(prompt) is None


@pytest.mark.parametrize(
    "prompt,expected_action",
    [
        ("What is on my calendar?", "list_events"),
        ("What's on my calendar?", "list_events"),
        ("List my calendar events", "list_events"),
        ("Show all my calendar events", "list_events"),
        ("Show my calendar", "list_events"),
        ("Read my calendar events", "list_events"),
        ("List my calendars", "list_calendars"),
    ],
)
def test_calendar_read_operations_resolve_to_safe_read_actions(prompt, expected_action):
    read_op = required_read_operation_for_request(prompt)
    assert read_op is not None
    assert read_op.tool == "manage_calendar"
    assert read_op.args["action"] == expected_action


@pytest.mark.parametrize(
    "prompt",
    [
        "Open the calendar panel",
        "Open calendar",
        "Open my calendar",
        "Show the calendar panel",
        "Show calendar view",
        "Bring up my calendar",
        "Pop open the calendar panel",
    ],
)
def test_explicit_calendar_panel_navigation_routes_to_ui(prompt):
    intent = classify_tool_intent(prompt)
    assert intent.needs_tools is True
    assert intent.category == "ui"
    assert requested_capabilities(prompt) == {"ui"}
    assert _parse_explicit_open_panel_request(prompt) == (
        "ui_control",
        "open_panel calendar",
    )


def test_calendar_read_contract_exposes_only_manage_calendar_not_ui():
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "manage_calendar",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ui_control",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    for prompt in (
        "List my calendar events",
        "Show all my calendar events",
        "What events are in my calendar?",
        "Show my calendar",
    ):
        contract = resolve_turn_contract(
            capabilities=requested_capabilities(prompt),
            schemas=schemas,
            policy=ToolPolicy(),
            message=prompt,
        )
        assert contract.permits("manage_calendar")
        assert not contract.permits("ui_control")


@pytest.mark.parametrize(
    "prompt",
    [
        "How do I add an entry to my calendar?",
        "Can you explain how calendar reminders work?",
        "What are the latest current events in Europe?",
    ],
)
def test_explanatory_and_non_calendar_event_questions_do_not_false_positive(prompt):
    intent = classify_tool_intent(prompt)
    assert intent.category != "calendar"
    assert "calendar" not in requested_capabilities(prompt)
