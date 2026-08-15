"""Tests for slow_loop.py — the Phase-3 gate evidence journal."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory_platform"))
import slow_loop  # noqa: E402


@pytest.fixture
def env(tmp_path):
    old = slow_loop.JOURNAL_DIR
    slow_loop.JOURNAL_DIR = str(tmp_path / "journal")
    yield tmp_path
    slow_loop.JOURNAL_DIR = old


def test_run_journals(env):
    slow_loop.run("deploy-check", "abc", "success")
    assert any("deploy-check" in ln for ln in slow_loop.report())


def test_gate_not_met_without_evidence(env):
    g = slow_loop.gate()
    assert g["gate_met"] is False
    assert g["criterion_1"]["consecutive_successes"] < 30


def test_gate_streak_counts(env):
    for i in range(5):
        slow_loop.run(f"task{i % 2}", f"h{i}", "success")
    g = slow_loop.gate()
    assert g["criterion_1"]["consecutive_successes"] == 5
    assert g["criterion_1"]["tasks"] == 2


def test_gate_streak_breaks_on_failure(env):
    for i in range(3):
        slow_loop.run("task", f"h{i}", "success")
    slow_loop.run("task", "hf", "failure", llm_fallback=True)
    g = slow_loop.gate()
    assert g["criterion_1"]["consecutive_successes"] == 0  # streak broken


def test_gate_reports_phase3_spec_only(env):
    g = slow_loop.gate()
    assert "SPEC-ONLY" in g["note"]
