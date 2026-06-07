"""Tests for Tier 2 LLM self-eval (teacher_escalation)."""

from src.teacher_escalation import _parse_tier2_response


def test_parse_tier2_pass():
    assert _parse_tier2_response("PASS the tool completed successfully") == ("ok", None)
    assert _parse_tier2_response("PASS") == ("ok", None)
    assert _parse_tier2_response("pass all good") == ("ok", None)
    assert _parse_tier2_response("") == ("ok", None)
    assert _parse_tier2_response(None) == ("ok", None)
    assert _parse_tier2_response(123) == ("ok", None)


def test_parse_tier2_fail():
    status, reason = _parse_tier2_response("FAIL the tool returned an error")
    assert status == "failure"
    assert "tool returned an error" in reason

    status, reason = _parse_tier2_response("fail   ")
    assert status == "failure"
    assert reason == "LLM self-eval flagged failure"

    status, reason = _parse_tier2_response("FAIL: connection refused")
    assert status == "failure"
    assert "connection refused" in reason


def test_parse_tier2_inconclusive():
    assert _parse_tier2_response("I'm not sure") == ("ok", None)
    assert _parse_tier2_response("Maybe it worked") == ("ok", None)
    assert _parse_tier2_response("yes") == ("ok", None)
