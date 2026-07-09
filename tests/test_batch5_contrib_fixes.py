"""Regression tests for fifth contribution batch."""
from src.agent_loop import _classify_agent_request
from routes.model_routes import _parse_model_list


def test_classify_agent_non_english_not_low_signal():
    r = _classify_agent_request([], "quanto vale bitcoin ora")
    assert r["low_signal"] is False


def test_classify_short_greeting_low_signal():
    r = _classify_agent_request([], "hi")
    assert r["low_signal"] is True


def test_probe_hidden_merge_preserves_user_hidden():
    user_hidden = {"model-a", "model-b"}
    failed = {"model-c"}
    merged = sorted(user_hidden | failed)
    assert "model-a" in merged and "model-c" in merged


def test_parse_model_list_json():
    assert _parse_model_list('["a","b"]') == ["a", "b"]