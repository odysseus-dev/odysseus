"""Configurable synthesis / final-report LLM timeout (#6986).

Deep Research synthesis and final-report calls go through the non-streaming
``llm_call_async`` lane: llama.cpp-style servers buffer the whole completion,
so prefill + decode must finish inside the per-call read timeout or the client
cancels mid-generation — and each of the 3 retries re-runs the identical
work. The former hardcoded ``timeout=180`` could never fit a full-length
report on a ~30 t/s local backend (needs ~290s+), so:

* ``DeepResearcher(llm_timeout=...)`` must clamp like the sibling
  research timeouts and default to 180 (behaviour unchanged for callers
  that don't opt in — scheduled research, services/research copy),
* ``research_handler`` must resolve the ``research_llm_timeout_seconds``
  setting and pass it through,
* ``_synthesize`` / ``_final_report`` (both the initial write and the
  expansion retry) must forward ``self.llm_timeout`` to ``_llm``.
"""

from __future__ import annotations

import pytest

from src.deep_research import DeepResearcher


def _researcher(**kwargs) -> DeepResearcher:
    return DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        **kwargs,
    )


def test_llm_timeout_defaults_to_180():
    assert _researcher().llm_timeout == 180


def test_llm_timeout_is_clamped_like_sibling_timeouts():
    assert _researcher(llm_timeout=600).llm_timeout == 600
    assert _researcher(llm_timeout=0).llm_timeout == 180  # 0 → constructor default
    assert _researcher(llm_timeout=10).llm_timeout == 15  # lower bound
    assert _researcher(llm_timeout=99999).llm_timeout == 3600  # upper bound


def test_settings_declares_research_llm_timeout():
    from src.settings import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["research_llm_timeout_seconds"] == 180


@pytest.mark.asyncio
async def test_synthesis_uses_configured_llm_timeout():
    researcher = _researcher(llm_timeout=456)
    captured = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        captured.append(timeout)
        return "synthesized report body"

    researcher._llm = fake_llm

    result = await researcher._synthesize(
        "question",
        findings=[{"url": "https://example.test", "title": "t", "summary": "s"}],
        current_report="",
    )

    assert result == "synthesized report body"
    assert captured == [456]


@pytest.mark.asyncio
async def test_final_report_uses_configured_llm_timeout():
    researcher = _researcher(llm_timeout=789)
    captured = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        captured.append(timeout)
        # Short first answer → the expansion follow-up call must use the
        # same configured budget, not a residual literal.
        return "brief report"

    researcher._llm = fake_llm

    result = await researcher._final_report("question", "evolving report")

    assert result  # either the expansion or the original short report
    assert captured and all(timeout == 789 for timeout in captured)


def test_research_handler_resolver_bounds_and_falls_back(monkeypatch):
    """research_handler must read research_llm_timeout_seconds, bounded (#5049 pattern)."""
    import src.settings as settings_mod
    import src.research_handler as handler_mod

    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "600")
    assert handler_mod._resolve_llm_timeout() == 600

    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: "bogus")
    assert handler_mod._resolve_llm_timeout() == 180

    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: None)
    assert handler_mod._resolve_llm_timeout() == 180

    monkeypatch.setattr(settings_mod, "get_setting", lambda key, default=None: 99999)
    assert handler_mod._resolve_llm_timeout() == 3600
