"""Tests for topic keyword matching (src/topic_analyzer.py)."""
from types import SimpleNamespace

from src.topic_analyzer import analyze_topics


def _sm(*messages):
    history = [{"role": "user", "content": c} for c in messages]
    return SimpleNamespace(sessions={"s1": {"owner": None, "name": "S", "history": history}})


def _freq(result):
    return {t["topic"]: t["frequency"] for t in result["topics"]}


def test_substring_does_not_false_match_technology():
    # Regression: "ai" matched inside "email"/"again"/"rain"/"wait", flagging
    # Technology for messages with no technical content at all.
    result = analyze_topics(_sm("Can you send me an email again about the rain? I will wait."))
    assert "Technology" not in _freq(result)


def test_real_keywords_still_match():
    result = analyze_topics(_sm("I wrote some Python code to test the algorithm."))
    assert _freq(result).get("Technology", 0) >= 1


def test_multiword_keyword_matches():
    result = analyze_topics(_sm("Can you explain how to set this up?"))
    assert "Learning" in _freq(result)
