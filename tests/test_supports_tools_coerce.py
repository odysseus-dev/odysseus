"""Tri-state coercion for the PATCH /model-endpoints/{id} supports_tools field.

The Added Models "Tools: Auto/On/Off" toggle (#5206) PATCHes supports_tools as
true / false / null. _coerce_supports_tools maps that onto the stored flag:
True (force native schemas), False (force text parsing), or None (Auto, the
heuristic default) for anything unrecognized, including JSON null.
"""
import pytest

from routes.model_routes import _coerce_supports_tools


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        ("true", True),
        (1, True),
        (False, False),
        ("false", False),
        (0, False),
    ],
)
def test_recognized_values_map_to_bool(value, expected):
    assert _coerce_supports_tools(value) is expected


@pytest.mark.parametrize("value", [None, "auto", "bogus", 2, -1, "", [], {}])
def test_unrecognized_values_map_to_none(value):
    # null (Auto) and any junk fall back to the heuristic, never a stray bool.
    assert _coerce_supports_tools(value) is None


def test_cycle_matches_the_ui_walk():
    # The button cycles Auto -> On -> Off -> Auto, sending null -> true -> false
    # -> null; each lands on the intended stored flag.
    assert _coerce_supports_tools(None) is None       # Auto
    assert _coerce_supports_tools(True) is True        # On
    assert _coerce_supports_tools(False) is False      # Off
    assert _coerce_supports_tools(None) is None        # back to Auto
