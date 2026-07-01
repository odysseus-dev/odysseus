"""Tests for _parse_tool_args in src.tool_utils."""

import pytest
from src.tool_utils import _parse_tool_args


def test_dict_string():
    assert _parse_tool_args('{"key": "val"}') == {"key": "val"}


def test_empty_string_returns_empty_dict():
    assert _parse_tool_args("") == {}
    assert _parse_tool_args("   ") == {}


def test_dict_passthrough():
    d = {"a": 1}
    assert _parse_tool_args(d) is d


def test_body_envelope_unwrapped():
    inner = {"action": "do_thing", "arg": 1}
    assert _parse_tool_args({"body": inner}) == inner


def test_body_envelope_not_unwrapped_without_action():
    # "body" key present but inner dict has no "action" — keep as-is
    d = {"body": {"text": "hello"}}
    assert _parse_tool_args(d) == d


def test_array_raises():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_tool_args("[1, 2, 3]")


def test_int_raises():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_tool_args("42")


def test_bool_raises():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_tool_args("true")


def test_null_raises():
    with pytest.raises(ValueError, match="JSON object"):
        _parse_tool_args("null")


def test_bad_json_raises():
    with pytest.raises(ValueError):
        _parse_tool_args("{bad json}")


def test_non_string_non_dict_returns_empty_dict():
    # None, list, int passed directly (not as JSON string)
    assert _parse_tool_args(None) == {}
