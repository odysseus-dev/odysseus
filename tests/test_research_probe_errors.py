"""Regression tests for Deep Research model probe error messages.

Deep Research probes the selected model before starting a long run. When the
upstream returned a concrete model/API error, the probe used to collapse it into
"Cannot reach model", hiding the real issue from the UI.
"""
import pytest
from fastapi import HTTPException

from src.research_handler import ResearchHandler, _format_probe_failure


def test_probe_failure_preserves_upstream_model_errors():
    exc = HTTPException(
        status_code=400,
        detail="OpenAI returned HTTP 400: Unsupported parameter: temperature",
    )

    msg = _format_probe_failure("o3-mini", exc)

    assert msg == (
        "Model 'o3-mini' probe failed: "
        "OpenAI returned HTTP 400: Unsupported parameter: temperature"
    )


def test_probe_failure_keeps_api_key_guidance():
    exc = HTTPException(status_code=401, detail="OpenAI authentication failed")

    assert _format_probe_failure("gpt-4o", exc) == (
        "Model 'gpt-4o' requires an API key. Check your endpoint configuration."
    )


def test_probe_failure_keeps_reachability_guidance_for_plain_errors():
    msg = _format_probe_failure("local-model", RuntimeError("connection refused"))

    assert msg == "Cannot reach model 'local-model' — connection refused"


@pytest.mark.asyncio
async def test_probe_endpoint_surfaces_http_exception_detail(monkeypatch):
    async def _raise(*args, **kwargs):
        raise HTTPException(
            status_code=400,
            detail="OpenAI returned HTTP 400: max_tokens is not supported",
        )

    monkeypatch.setattr("src.llm_core.llm_call_async", _raise)

    with pytest.raises(RuntimeError) as excinfo:
        await ResearchHandler._probe_endpoint(
            "https://api.openai.com/v1/chat/completions",
            "o3-mini",
            {"Authorization": "Bearer test"},
        )

    msg = str(excinfo.value)
    assert "Model 'o3-mini' probe failed" in msg
    assert "max_tokens is not supported" in msg
    assert "Cannot reach model" not in msg


@pytest.mark.asyncio
async def test_probe_endpoint_uses_configured_timeout(monkeypatch):
    captured = {}

    async def _ok(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return "ok"

    monkeypatch.setattr("src.llm_core.llm_call_async", _ok)

    await ResearchHandler._probe_endpoint(
        "http://local.test/v1/chat/completions",
        "local-model",
        timeout=123,
    )

    assert captured["timeout"] == 123


@pytest.mark.asyncio
async def test_call_research_service_uses_and_clamps_probe_timeout(monkeypatch):
    captured_timeout = None

    # Use *args and **kwargs to avoid positional/keyword argument mismatches with self
    async def mock_probe_endpoint(*args, **kwargs):
        nonlocal captured_timeout
        captured_timeout = kwargs.get("timeout")
        raise RuntimeError("stop_early_stub")

    monkeypatch.setattr(ResearchHandler, "_probe_endpoint", mock_probe_endpoint)
    handler = ResearchHandler()

    # 1. Assert it successfully picks up a standard configured timeout value
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: 75 if key == "research_probe_timeout_seconds" else default
    )
    with pytest.raises(RuntimeError, match="stop_early_stub"):
        await handler.call_research_service(
            query="test", llm_endpoint="http://local.test", llm_model="test-model"
        )
    assert captured_timeout == 75

    # 2. Assert it clamps an out-of-range low configuration value to the minimum (15)
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: 5 if key == "research_probe_timeout_seconds" else default
    )
    with pytest.raises(RuntimeError, match="stop_early_stub"):
        await handler.call_research_service(
            query="test", llm_endpoint="http://local.test", llm_model="test-model"
        )
    assert captured_timeout == 15

    # 3. Assert it clamps an out-of-range high configuration value to the maximum (3600)
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: 5000 if key == "research_probe_timeout_seconds" else default
    )
    with pytest.raises(RuntimeError, match="stop_early_stub"):
        await handler.call_research_service(
            query="test", llm_endpoint="http://local.test", llm_model="test-model"
        )
    assert captured_timeout == 3600
