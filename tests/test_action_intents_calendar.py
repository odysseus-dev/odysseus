"""Regression tests for issue #6184 — Calendar data reads vs panel navigation."""

import pytest
from src.action_intents import classify_tool_intent


CALENDAR_DATA_READS = [
    ("List my calendar events", "calendar events list request"),
    ("Show all my calendar events", "calendar events list request"),
    ("Read my calendar schedule", "calendar events list request"),
    ("What events are in my calendar?", "calendar events question"),
    ("What meetings are on the calendar?", "calendar events question"),
    ("What is in my calendar?", "calendar content question"),
    ("What is on my calendar?", "calendar agenda question"),
    ("Show my schedule", "calendar schedule list request"),
    ("List my appointments", "calendar schedule list request"),
    ("View the calendar agenda", "calendar events list request"),
]


CALENDAR_PANEL_NAV = [
    "Show my calendar",
    "Open my calendar",
    "Bring up the calendar",
    "Show calendar",
]


NON_CALENDAR = [
    "List world events",
    "What events happened in 2020",
]


@pytest.mark.parametrize("text,expected_reason", CALENDAR_DATA_READS)
def test_calendar_data_read_routing(text, expected_reason):
    intent = classify_tool_intent(text)
    assert intent.needs_tools is True
    assert intent.category == "calendar"
    assert intent.reason == expected_reason


@pytest.mark.parametrize("text", CALENDAR_PANEL_NAV)
def test_calendar_panel_navigation(text):
    intent = classify_tool_intent(text)
    assert intent.needs_tools is True
    assert intent.category == "ui"
    assert intent.reason == "open/show panel request"


@pytest.mark.parametrize("text", NON_CALENDAR)
def test_calendar_false_negatives(text):
    intent = classify_tool_intent(text)
    assert intent.needs_tools is False
