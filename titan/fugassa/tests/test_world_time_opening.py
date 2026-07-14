"""Phase 3 — wizard opening_time_hint → world_time mapping."""

from titan.fugassa.game_bootstrap import apply_opening_time_hint_to_world_time


_OPENING_TABLE = """| Time of Day | HH:MM AM/PM | Era, Year, Month, Day | Moon Phase | Current Location | Season | Weather |
|---|---|---|---|---|---|---|
| Dawn | 6:30 AM | Age of Sail, Year 42, March, Day 3 | Waxing | Harbor | Spring | Mist |"""


def test_apply_opening_time_overwrite_fills_all_fields():
    state = {
        "world_profile": {"opening_time_hint": _OPENING_TABLE},
        "world_time": {"day": 1, "hour": 8},
    }
    assert apply_opening_time_hint_to_world_time(state, overwrite=True) is True
    wt = state["world_time"]
    assert wt["time_of_day"] == "Dawn"
    assert wt["hhmm"] == "6:30 AM"
    assert wt["hour"] == 6
    assert wt["era"] == "Age of Sail"
    assert wt["year"] == "Year 42"
    assert wt["month"] == "March"
    assert wt["day"] == "Day 3"
    assert wt["moon_phase"] == "Waxing"
    assert wt["season"] == "Spring"
    assert wt["weather"] == "Mist"


def test_apply_opening_time_no_hint_is_noop():
    state = {"world_profile": {}, "world_time": {"hour": 9}}
    assert apply_opening_time_hint_to_world_time(state, overwrite=True) is False
    assert state["world_time"]["hour"] == 9
