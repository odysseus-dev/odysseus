from src.action_intents import classify_tool_intent


def test_open_cal_promotes_to_ui_panel():
    intent = classify_tool_intent("open cal")

    assert intent.needs_tools
    assert intent.category == "ui"


def test_terse_dated_calendar_create_promotes_to_calendar():
    intent = classify_tool_intent("add fireworks october 3rd")

    assert intent.needs_tools
    assert intent.category == "calendar"


def test_terse_timed_calendar_create_promotes_to_calendar():
    intent = classify_tool_intent("schedule dinner next friday 7pm")

    assert intent.needs_tools
    assert intent.category == "calendar"
