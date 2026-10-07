from src.agent_loop import _parse_ambiguous_calendar_date_ask_user


def test_next_month_ordinal_weekday_is_not_treated_as_missing_date() -> None:
    prompt = (
        "Create a calendar event on the last Wednesday of next month at "
        "3:15 PM."
    )

    assert _parse_ambiguous_calendar_date_ask_user(prompt) is None


def test_genuinely_missing_next_month_day_still_asks_user() -> None:
    prompt = "Create a dinner reservation next month at 7 PM."

    tool_call = _parse_ambiguous_calendar_date_ask_user(prompt)

    assert tool_call is not None
    assert tool_call[0] == "ask_user"
