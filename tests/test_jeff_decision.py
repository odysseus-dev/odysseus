"""Tests for the jeff-serve decision client and the email-urgency adapter path.

Covers the calibrated-score mapping plus the two high-precision overrides
(promotions and "waiting outside") that the prompt path also applies. All
network is mocked, so these run offline.
"""

import asyncio

from src import builtin_actions as ba
from src import jeff_decision as jd


def _answer(score):
    return {"urgency": {"type": "score", "score": score, "probabilities": {},
                        "legend": {}, "confidence": 0.9}}


def _run(coro):
    return asyncio.run(coro)


def _enable(monkeypatch, score):
    monkeypatch.setenv("ODYSSEUS_JEFF_EMAIL_URGENCY", "1")

    async def fake(*a, **k):
        return _answer(score)

    monkeypatch.setattr(jd, "jeff_systemone", fake)


def test_score_value_and_choice_of():
    assert jd.score_value(_answer(2.5)["urgency"]) == 2.5
    assert jd.score_value({"type": "choice", "probabilities": {"a": 0.25, "b": 0.75}}) == 0.75
    assert jd.choice_of({"type": "choice", "choice": "b"}) == "b"
    assert jd.score_value(None) is None
    assert jd.choice_of(None) is None


def test_non_finite_score_falls_back(monkeypatch):
    for bad in (float("nan"), float("inf"), "nan"):
        assert jd.score_value({"type": "score", "score": bad}) is None
        _enable(monkeypatch, bad)
        assert _run(ba._spec_triage_email_urgency({"subject": "x", "body": "y"}, triage_version=11)) is None


def test_disabled_job_does_not_call_adapter(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_JEFF_EMAIL_URGENCY", "0")
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return _answer(4)

    monkeypatch.setattr(jd, "jeff_systemone", fake)
    out = _run(ba._spec_triage_email_urgency({"subject": "x", "body": "y"}, triage_version=11))
    assert out is None
    assert calls["n"] == 0


def test_high_score_maps_to_urgent(monkeypatch):
    _enable(monkeypatch, 4)
    v = _run(ba._spec_triage_email_urgency(
        {"subject": "Locked out", "from": "A <a@b>", "body": "help now", "uid": "1", "message_id": "m"},
        triage_version=11))
    assert v["score"] == 3 and v["tags"] == ["urgent"] and v["triage_version"] == 11


def test_low_score_maps_to_zero(monkeypatch):
    _enable(monkeypatch, 0)
    v = _run(ba._spec_triage_email_urgency({"subject": "FYI", "body": "no action needed", "uid": "2"},
                                           triage_version=11))
    assert v["score"] == 0 and v["tags"] == []


def test_promotion_is_overridden_to_zero(monkeypatch):
    _enable(monkeypatch, 4)
    v = _run(ba._spec_triage_email_urgency(
        {"subject": "FLASH SALE", "body": "discount, limited time, shop now, coupon", "uid": "3"},
        triage_version=11))
    assert v["score"] == 0 and v["reason"].startswith("bulk")


def test_waiting_outside_forces_urgent(monkeypatch):
    _enable(monkeypatch, 0.2)
    v = _run(ba._spec_triage_email_urgency(
        {"subject": "Deliveries", "body": "I am waiting outside, can't get in", "uid": "4"},
        triage_version=11))
    assert v["score"] == 3


def test_adapter_unavailable_returns_none(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_JEFF_EMAIL_URGENCY", "1")

    async def fake(*a, **k):
        return None

    monkeypatch.setattr(jd, "jeff_systemone", fake)
    assert _run(ba._spec_triage_email_urgency({"subject": "x", "body": "y"}, triage_version=11)) is None
