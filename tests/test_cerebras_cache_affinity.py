"""Regression test for issue #4640.

Cerebras endpoints must not receive llama.cpp-specific fields
(session_id, cache_prompt) even when endpoint_kind is misconfigured as 'local'.
"""
import importlib


def test_detect_provider_recognizes_cerebras():
    """_detect_provider should return 'cerebras' for api.cerebras.ai URLs."""
    llm_core = importlib.import_module("src.llm_core")
    assert llm_core._detect_provider("https://api.cerebras.ai/v1") == "cerebras"


def test_cerebras_not_self_hosted():
    """_is_self_hosted_openai_compatible should be False for Cerebras."""
    llm_core = importlib.import_module("src.llm_core")
    assert llm_core._is_self_hosted_openai_compatible("https://api.cerebras.ai/v1") is False


def test_apply_local_cache_affinity_skips_cerebras():
    """_apply_local_cache_affinity must not add session_id/cache_prompt for Cerebras."""
    llm_core = importlib.import_module("src.llm_core")
    payload = {"messages": []}
    llm_core._apply_local_cache_affinity(payload, "https://api.cerebras.ai/v1", "test-session-123")
    assert "session_id" not in payload, "session_id leaked into Cerebras payload"
    assert "cache_prompt" not in payload, "cache_prompt leaked into Cerebras payload"
