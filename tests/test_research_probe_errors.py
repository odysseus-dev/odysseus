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
