"""Regression tests for the configurable LLM connect timeout.

Background: chat uses the streaming path, which (unlike llm_call) does not retry
a connect error -- it marks the host and emits a 503 immediately. With the old
hard-coded connect=3.0s, a brief blip on the first (cold) connect of an idle
chat to an offshore/public endpoint surfaced as an intermittent 503 that cleared
on resend. The connect budget is now LLMConfig.CONNECT_TIMEOUT (env
LLM_CONNECT_TIMEOUT), applied via _call_timeout/_stream_timeout helpers.
"""
import importlib
import httpx
import pytest

from src import llm_core
from src.llm_core import LLMConfig, _call_timeout, _stream_timeout


def test_default_connect_timeout_is_widened_not_three():
    # Regression guard: must not regress to the old too-tight 3.0s default.
    assert LLMConfig.CONNECT_TIMEOUT >= 8.0
    assert LLMConfig.CONNECT_TIMEOUT != 3.0
    assert LLMConfig.CONNECT_TIMEOUT == 10.0


def test_call_timeout_uses_config_connect_and_passes_read():
    t = _call_timeout(45)
    assert isinstance(t, httpx.Timeout)
    assert t.connect == LLMConfig.CONNECT_TIMEOUT
    assert t.read == 45.0
    assert t.write == 10.0
    assert t.pool == 5.0


def test_stream_timeout_uses_config_connect_and_passes_read():
    t = _stream_timeout(300)
    assert isinstance(t, httpx.Timeout)
    assert t.connect == LLMConfig.CONNECT_TIMEOUT
    assert t.read == 300.0
    assert t.write == 30.0
    assert t.pool == 5.0


def test_helpers_are_config_driven(monkeypatch):
    # Helpers read LLMConfig at call time, so ops can tune without code edits.
    monkeypatch.setattr(LLMConfig, "CONNECT_TIMEOUT", 4.5)
    assert _call_timeout(30).connect == 4.5
    assert _stream_timeout(30).connect == 4.5


def test_env_override_is_honoured(monkeypatch):
    monkeypatch.setenv("LLM_CONNECT_TIMEOUT", "6.5")
    reloaded = importlib.reload(llm_core)
    try:
        assert reloaded.LLMConfig.CONNECT_TIMEOUT == 6.5
    finally:
        monkeypatch.delenv("LLM_CONNECT_TIMEOUT", raising=False)
        importlib.reload(llm_core)  # restore module-level default for other tests
