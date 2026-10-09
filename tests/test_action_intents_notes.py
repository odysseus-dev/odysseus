"""Regression tests for issue #6186 — read-only Notes requests must reach
manage_notes instead of staying in plain chat or opening the panel."""

import pytest

from src.action_intents import classify_tool_intent


# Clear data reads: chat must escalate to agent mode so manage_notes can
# answer with a list, a search result, or an explicit policy error.
NOTES_READS = [
    "What are my notes?",
    "Read my notes",
    "list my notes",
    "Show me my notes",
    "show my notes",
    "get my notes",
    "view my checklist",
    "list my todos",
    "read my todo list",
    "search my notes for dentist",
    "what's in my notes",
    "whats on my notes list?",
    "what did I write in my notes",
    "find my note about groceries",
]

# Panel navigation keeps its ui_control meaning.
NOTES_PANEL_NAV = [
    "open my notes",
    "Open the Notes panel",
    "bring up my notes",
]

# Definition / explanatory questions stay in plain chat.
NOTES_NOT_A_LOOKUP = [
    "What is a note?",
    "What are notes used for?",
    "How do I add a note?",
    "Can you explain how notes work?",
]


@pytest.mark.parametrize("text", NOTES_READS)
def test_notes_read_requests_route_to_manage_notes(text):
    intent = classify_tool_intent(text)

    assert intent.needs_tools
    assert intent.category == "notes"
    assert intent.reason in {
        "notes read request",
        "notes read question",
        "notes content question",
    }


@pytest.mark.parametrize("text", NOTES_PANEL_NAV)
def test_notes_panel_navigation_stays_ui(text):
    intent = classify_tool_intent(text)

    assert intent.needs_tools
    assert intent.category == "ui"


@pytest.mark.parametrize("text", NOTES_NOT_A_LOOKUP)
def test_notes_definition_questions_stay_plain_chat(text):
    intent = classify_tool_intent(text)

    assert not intent.needs_tools


def test_note_actions_still_promote_to_notes():
    intent = classify_tool_intent("add milk to my todo list")

    assert intent.needs_tools
    assert intent.category == "notes"


def test_notes_read_does_not_hijack_calendar_or_code_requests():
    # "show my calendar" stays on its existing route; only the Notes noun
    # is re-routed by this change.
    assert classify_tool_intent("show my calendar").category != "notes"
    assert classify_tool_intent("open my calendar").category == "ui"
    assert classify_tool_intent("Edit the notes.py file and run its tests.").category == "workspace"
