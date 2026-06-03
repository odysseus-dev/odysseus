"""SKILL.md scalar parsing must not corrupt versions / identifier-like values.

_parse_scalar coerced any dotted token with float(), so a two-part version
like "1.10" became 1.1 and round-tripped (on save) to "1.1" — the 10th minor
version silently became the 1st. Numbers should only be coerced when they
round-trip losslessly.
"""
from services.memory.skill_format import _parse_scalar


def test_two_part_version_with_trailing_zero_preserved():
    assert _parse_scalar("1.10") == "1.10"
    assert _parse_scalar("2.20") == "2.20"


def test_three_part_version_stays_string():
    assert _parse_scalar("1.0.0") == "1.0.0"


def test_real_floats_still_coerced():
    assert _parse_scalar("1.5") == 1.5
    assert _parse_scalar("3.0") == 3.0


def test_ints_and_leading_zeros():
    assert _parse_scalar("2") == 2
    assert _parse_scalar("01") == "01"  # leading zero preserved, not -> 1


def test_bools_and_quotes_unaffected():
    assert _parse_scalar("true") is True
    assert _parse_scalar('"1.10"') == "1.10"
