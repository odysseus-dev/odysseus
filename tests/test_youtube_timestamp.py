"""Transcript timestamps must not overflow the minutes field past an hour.

The old `MM:SS` formatting produced e.g. 75:30 for 1h15m30s on long videos;
`_format_timestamp` switches to H:MM:SS once the offset passes an hour.
"""
from src.youtube_handler import _format_timestamp


def test_under_one_hour():
    assert _format_timestamp(330) == "05:30"
    assert _format_timestamp(59) == "00:59"


def test_over_one_hour():
    assert _format_timestamp(4530) == "1:15:30"  # was "75:30"


def test_exactly_one_hour():
    assert _format_timestamp(3600) == "1:00:00"


def test_zero():
    assert _format_timestamp(0) == "00:00"


def test_float_offset_truncates():
    assert _format_timestamp(90.9) == "01:30"
