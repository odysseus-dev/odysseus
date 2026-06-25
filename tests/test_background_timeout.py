"""Regression: background LLM tasks (memory skill-extraction, research query/
plan synthesis) used hard-coded read timeouts (30s / 15s). On slow local
backends those tasks exhausted MAX_RETRIES and failed with a 502 (#4610).

LLMConfig.background_timeout(default) keeps each call's default when the env is
unset, and lets LLM_BACKGROUND_TIMEOUT override all of them at once.
"""
from src.llm_core import LLMConfig


def test_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("LLM_BACKGROUND_TIMEOUT", raising=False)
    assert LLMConfig.background_timeout(30) == 30
    assert LLMConfig.background_timeout(15) == 15


def test_env_overrides_every_default(monkeypatch):
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "180")
    assert LLMConfig.background_timeout(30) == 180
    assert LLMConfig.background_timeout(15) == 180


def test_float_env_is_truncated_to_int(monkeypatch):
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "90.5")
    assert LLMConfig.background_timeout(30) == 90


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "abc")
    assert LLMConfig.background_timeout(30) == 30


def test_empty_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "")
    assert LLMConfig.background_timeout(15) == 15


def test_zero_or_negative_is_floored_to_one(monkeypatch):
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "0")
    assert LLMConfig.background_timeout(30) == 1
    monkeypatch.setenv("LLM_BACKGROUND_TIMEOUT", "-5")
    assert LLMConfig.background_timeout(30) == 1
