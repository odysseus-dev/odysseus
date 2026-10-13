from src.agent_loop import _explicitly_named_personal_tools


def test_extracts_exact_personal_tool_names():
    text = "Call manage_calendar once, then use manage_notes."
    assert _explicitly_named_personal_tools(text) == {
        "manage_calendar",
        "manage_notes",
    }


def test_does_not_match_substrings_or_generic_calendar_words():
    assert _explicitly_named_personal_tools("Show my calendar") == set()
    assert _explicitly_named_personal_tools("xmanage_calendar_backup") == set()
