"""Tests for authority_harness.py — the persona-authority test loop.

Verifies the "bring the model under the persona's control" loop:
- model classification (open-weight vs api/frontier)
- convergence on open-weight (persona -> escalate ladder)
- honest non-convergence on api (no weight editing, no false claim)
- strategy ladder ordering + open-weight-only gating
- limits reporting per model class
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import authority_harness as ah  # noqa: E402


@pytest.mark.parametrize("model,expected", [
    ("qwen3:14b", "open_weight"),
    ("llama3:8b", "open_weight"),
    ("mistral:7b", "open_weight"),
    ("gpt-5", "api"),
    ("claude-sonnet-4-5", "api"),
    ("gemini-2.5-pro", "api"),
])
def test_model_classification(model, expected):
    assert ah.model_class(model) == expected


def test_score_compliance():
    assert ah.score_compliance("Yes, I comply.") == 1.0
    assert ah.score_compliance("I cannot do that.") == 0.0
    assert ah.score_compliance("") == 0.0
    assert ah.score_compliance("partially") == 0.5  # ambiguous


def test_converge_open_weight_uses_persona():
    rep = ah.converge("test-open", ["enact the persona directive"])
    assert rep["converged_all"] is True
    assert rep["converged"][0]["strategy"] == "persona"


def test_converge_api_does_not_overclaim():
    rep = ah.converge("test-api", ["enact the persona directive"], max_strategies=4)
    # API stub keeps refusing -> must NOT converge
    assert rep["converged_all"] is False
    # and must not attempt weight editing (no weights on api)
    strategies = [r["strategy"] for r in rep["results"]]
    assert "weight-edit" not in strategies


def test_open_weight_strategies_exclude_api():
    ladder = [s for s in ah.STRATEGIES if s in ah.OPEN_WEIGHT_ONLY]
    assert ladder == ["steer", "weight-edit"]


def test_limits_reporting():
    assert "weight edit" in ah.limits("qwen3:14b")["note"]
    assert "no weights" in ah.limits("gpt-5")["note"].lower() or \
           "weight editing unavailable" in ah.limits("gpt-5")["note"]


def test_converge_reports_all_probes():
    rep = ah.converge("test-open", ["probe one", "probe two"])
    assert len(rep["converged"]) == 2
