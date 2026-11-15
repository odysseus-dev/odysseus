"""DR-4.1 Targeted Reliability, Verifier Hardening & Budget Guard Tests."""

from __future__ import annotations

import time
import pytest

from src.deep_research import DeepResearcher
from src.research_verifier import verify_report_evidence


def test_dr4_1_constructor_initializes_verification_state():
    r = DeepResearcher(llm_endpoint="http://127.0.0.1:11434/v1", llm_model="gemma4:31b")
    assert r.verification_status == "not_run"
    assert r.verification_summary is None
    assert r.verification_error is None
    assert r.warmup_large_model is False


@pytest.mark.asyncio
async def test_dr4_1_early_exit_retains_initialized_verification_state(monkeypatch):
    r = DeepResearcher(llm_endpoint="http://127.0.0.1:11434/v1", llm_model="gemma4:31b", max_rounds=1)
    r.simple_research_mode = False

    async def _plan_strat(q): return "Plan"
    async def _classify(q, p): return "technical"
    async def _plan(q, rep, rnd): return []
    async def _gen_q(q, rep, rnd): return []

    monkeypatch.setattr(r, "_create_plan", _plan_strat)
    monkeypatch.setattr(r, "_classify_category", _classify)
    monkeypatch.setattr(r, "_plan_research_actions", _plan)
    monkeypatch.setattr(r, "_generate_queries", _gen_q)

    out = await r.research("Early exit zero queries test")
    assert "No information could be gathered" in out
    assert r.verification_status == "not_run"
    assert r.verification_summary is None


@pytest.mark.asyncio
async def test_dr4_1_budget_aware_llm_retry_guard_bounds_retries_and_timeout(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_time=45,
    )
    r._start_time = time.time() - 20.0  # 25s remaining budget
    captured: dict = {}

    async def _fake_llm_call_async(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_llm_call_async)
    res = await r._llm([{"role": "user", "content": "hi"}], timeout=120)
    assert res == "ok"
    assert captured["timeout"] <= 25
    assert captured["max_retries"] == 1

    r._start_time = time.time() - 50.0
    with pytest.raises(TimeoutError):
        await r._llm([{"role": "user", "content": "hi"}], timeout=120)


@pytest.mark.asyncio
async def test_dr4_1_optional_warmup_disabled_by_default_and_bounded_when_enabled(monkeypatch):
    called = {"count": 0}

    class _FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None):
            called["count"] += 1
            called["timeout"] = self.timeout
            class _Resp:
                status_code = 200
            return _Resp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)

    r_default = DeepResearcher(llm_endpoint="http://127.0.0.1:11434/v1", llm_model="gemma4:31b")
    assert r_default.warmup_large_model is False

    r_enabled = DeepResearcher(llm_endpoint="http://127.0.0.1:11434/v1", llm_model="gemma4:31b", max_time=100)
    r_enabled.warmup_large_model = True
    r_enabled.warmup_timeout = 18
    r_enabled._start_time = time.time()
    ok = await r_enabled._maybe_warmup_large_local_model()
    assert ok is True
    assert called["count"] == 1
    assert called["timeout"] <= 18.0


def test_dr4_1_verifier_excludes_bandwidth_rate_units_and_url_slugs():
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
            "title": "NVIDIA Blackwell Architecture Overview",
            "summary": "The RTX 5090 features 32 GB GDDR7 memory with 1,792 GB/s memory bandwidth and 575W TDP.",
        },
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
            "title": "NVIDIA Ada Architecture Overview",
            "summary": "The RTX 4090 features 24 GB GDDR6X memory with 1,008 GB/s memory bandwidth and 450W TDP.",
        },
    ]
    report = (
        "# Architecture Comparison\n"
        "- The RTX 5090 delivers 32 GB VRAM, 1,792 GB/s bandwidth, and 575W TDP "
        "([RTX 5090](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/)).\n"
        "- The RTX 4090 delivers 24 GB VRAM, 1,008 GB/s bandwidth, and 450W TDP "
        "([RTX 4090](https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/)).\n"
    )
    vr = verify_report_evidence(report, findings)
    assert vr.conflicting_evidence == []
    all_items = " | ".join(vr.verified_facts + vr.unsupported_claims)
    assert "architecture" not in all_items.lower()
    assert "rtx-5090" not in all_items.lower()
    assert "rtx-4090" not in all_items.lower()
    assert "RTX 5090: 32 GB" in vr.verified_facts
    assert "RTX 4090: 24 GB" in vr.verified_facts
