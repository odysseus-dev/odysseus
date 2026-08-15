"""Tests for growth_delta.py — Phase 1 behavioural growth deltas (fast loop).

Verifies the two distinct gates per the spec (memory-always-on-authority.md):
- behavioural deltas apply on a SINGLE high-confidence signal (conf >= 0.6)
- permanent topics (constitution/identity) are never routed to from a delta
- max 3 deltas per reflect pass
- deltas are journaled with evidence (auditable, never a silent write)
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import growth_delta as gd  # noqa: E402


@pytest.fixture(autouse=True)
def iso(tmp_path):
    """Isolate the deltas file to a temp dir for every test."""
    old = gd.DELTAS_FILE
    gd.DELTAS_FILE = str(tmp_path / "growth_deltas.json")
    gd._write_store = lambda text, topic: None  # never touch the real store
    yield tmp_path
    gd.DELTAS_FILE = old


def _delta(change, conf, target="delivery"):
    return {"change": change, "evidence": "test evidence", "confidence": conf,
            "target": target}


def test_single_high_confidence_delta_applies(iso):
    """Behavioural growth applies from ONE strong signal (fast loop)."""
    res = gd.apply(json.dumps(_delta(
        "when the user asks for a summary, lead with the verdict first", 0.8)))
    assert len(res["applied"]) == 1
    state = gd._load_deltas()
    assert len(state["applied"]) == 1
    assert state["applied"][0]["change"].startswith("when the user")
    assert state["applied"][0]["evidence"] == "test evidence"


def test_low_confidence_delta_rejected(iso):
    """The single-signal gate still has a confidence floor (0.6)."""
    res = gd.apply(json.dumps(_delta(
        "a weak single-signal delta that must not pass the gate", 0.4)))
    assert res["applied"] == []
    assert "below the 0.6" in res["error"]


def test_permanent_topics_never_routed(iso):
    """Behavioural deltas may target delivery/operating/persona only — a delta
    must never slip into permanent rule territory (constitution/identity)."""
    for topic in ("constitution", "identity", "project", "human"):
        res = gd.apply(json.dumps(_delta(
            f"an attempted delta toward {topic} that must be refused", 0.9, topic)))
        assert res["applied"] == [], f"delta leaked to permanent topic {topic}"
        assert "not a behavioural target" in res["error"]


def test_short_or_empty_delta_rejected(iso):
    res = gd.apply(json.dumps(_delta("too short", 0.9)))
    assert res["applied"] == []
    res = gd.apply(json.dumps({"change": "", "confidence": 0.9, "target": "delivery"}))
    assert res["applied"] == []


def test_reflect_applies_high_confidence_only(iso, monkeypatch):
    """reflect() routes LLM output through the same gates; only >=0.6 pass."""
    monkeypatch.setattr(gd, "_llm", lambda p: json.dumps({
        "deltas": [
            _delta("lead replies with the verdict then the basis", 0.9),
            _delta("a speculative one-off observation that should be skipped", 0.4),
        ]}))
    res = gd.reflect("some interaction material", dry_run=True)
    # dry run: nothing persisted, but the gate is visible
    assert len(res["would_apply"]) == 1
    assert res["would_apply"][0]["confidence"] == 0.9
    assert gd._load_deltas()["applied"] == []


def test_reflect_caps_at_three(iso, monkeypatch):
    """Spec: up to 3 actionable deltas per reflect pass."""
    monkeypatch.setattr(gd, "_llm", lambda p: json.dumps({
        "deltas": [_delta(f"delta number {i} a meaningful behaviour change", 0.9)
                   for i in range(5)]}))
    res = gd.reflect("material", dry_run=True)
    assert len(res["would_apply"]) == 3


def test_recent_reads_latest(iso):
    gd.apply(json.dumps(_delta("first delta with a distinct behaviour", 0.9)))
    gd.apply(json.dumps(_delta("second delta with a distinct behaviour", 0.9)))
    rec = gd.recent(limit=1)
    assert len(rec) == 1
    assert "second" in rec[0]["change"]


def test_invalid_delta_json_errors(iso):
    res = gd.apply("not json at all {{{")
    assert res["applied"] == []
    assert "invalid" in res["error"]
