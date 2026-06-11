from src.research_utils import is_low_quality


def test_is_low_quality_treats_non_string_as_low_quality():
    # Old code reached summary.lower(), hit AttributeError, and the bare
    # except returned False (fail open) so a malformed source slipped through
    # as "good". A non-string summary has no usable content, so it should be
    # filtered like an empty one (which already returns True).
    assert is_low_quality(123) is True
    assert is_low_quality({"bad": True}) is True
    assert is_low_quality(["does not contain"]) is True


def test_is_low_quality_still_classifies_strings():
    assert is_low_quality("This page does not contain relevant information") is True
    assert is_low_quality("Detailed analysis of the 2026 EV market") is False
