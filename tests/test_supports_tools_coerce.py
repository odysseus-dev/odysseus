"""Tri-state coercion for the PATCH /model-endpoints/{id} supports_tools field.

Coerces supported boolean, integer (1/0), and string values to True, False, or None.
Malformed or unrecognized values raise ValueError (surfaced as HTTP 400).
"""
import pytest

from routes.model_routes import _coerce_supports_tools


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("on", True),
        ("ON", True),
        ("yes", True),
        (1, True),
        ("1", True),
        (False, False),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("off", False),
        ("OFF", False),
        ("no", False),
        (0, False),
        ("0", False),
    ],
)
def test_recognized_values_map_to_bool(value, expected):
    assert _coerce_supports_tools(value) is expected


@pytest.mark.parametrize("value", [None, "auto", "AUTO", "null", "none", ""])
def test_auto_values_map_to_none(value):
    assert _coerce_supports_tools(value) is None


@pytest.mark.parametrize("value", ["bogus", 2, -1, 100, [], {}, [True], {"a": 1}])
def test_malformed_values_raise_value_error(value):
    with pytest.raises(ValueError):
        _coerce_supports_tools(value)


def test_cycle_matches_the_ui_walk():
    # The button cycles Auto -> On -> Off -> Auto (null -> true -> false -> null)
    assert _coerce_supports_tools(None) is None       # Auto
    assert _coerce_supports_tools(True) is True        # On
    assert _coerce_supports_tools(False) is False      # Off
    assert _coerce_supports_tools(None) is None        # back to Auto
