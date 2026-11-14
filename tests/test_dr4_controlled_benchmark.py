"""DR-4 Controlled End-to-End Benchmark & 7-Scenario Adversarial Suite."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.deep_research import DeepResearcher
from src.research_handler import ResearchHandler
from src.research_navigator import ResearchAction
from src.tools.research import do_manage_research


def _configure_mock_pipeline(
    researcher: DeepResearcher,
    monkeypatch,
    *,
    category: str,
    rounds_plan: dict[int, list[dict]],
    draft_report: str,
    final_report: str,
):
    researcher.simple_research_mode = False

    async def _fake_plan_strategy(question: str) -> str:
        return f"Structured plan for {category}: {question}"

    async def _fake_classify(question: str, plan: str) -> str:
        return category

    async def _fake_plan(question: str, report: str, round_num: int):
        return [ResearchAction("web_search", {"query": f"{category} round {round_num}"})]

    async def _fake_exec(actions, question: str):
        rnum = researcher.round_count
        items = rounds_plan.get(rnum, [])
        for it in items:
            if it.get("url"):
                researcher.urls_fetched.add(it["url"])
                researcher.analyzed_urls.append(it["url"])
        return items

    async def _fake_synthesize(question: str, findings, current_report: str) -> str:
        return draft_report

    async def _fake_should_stop(question: str, report: str, round_num: int) -> bool:
        return round_num >= max(rounds_plan.keys())

    async def _fake_final_report(question: str, report: str) -> str:
        return final_report

    monkeypatch.setattr(researcher, "_create_plan", _fake_plan_strategy)
    monkeypatch.setattr(researcher, "_classify_category", _fake_classify)
    monkeypatch.setattr(researcher, "_plan_research_actions", _fake_plan)
    monkeypatch.setattr(researcher, "_execute_research_actions", _fake_exec)
    monkeypatch.setattr(researcher, "_synthesize", _fake_synthesize)
    monkeypatch.setattr(researcher, "_should_stop", _fake_should_stop)
    monkeypatch.setattr(researcher, "_final_report", _fake_final_report)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_id,category,question,rounds_plan,draft_report,final_report,expected_verified,expected_unverified",
    [
        (
            "dr4bench0001",
            "comparison",
            "Compare NVIDIA RTX 4090 and RTX 5090 specifications",
            {
                1: [
                    {
                        "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
                        "title": "NVIDIA RTX 4090 Specifications",
                        "summary": "NVIDIA RTX 4090 includes 24 GB GDDR6X memory, 16384 CUDA cores, and 450W TDP.",
                        "round": 1,
                    },
                    {
                        "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
                        "title": "NVIDIA RTX 5090 Specifications",
                        "summary": "NVIDIA RTX 5090 includes 32 GB GDDR7 memory, 21760 CUDA cores, and 575W TDP.",
                        "round": 1,
                    },
                ]
            },
            "## Draft\n- RTX 4090 has 24 GB VRAM and 450W TDP.\n- RTX 5090 has 32 GB VRAM and 575W TDP.",
            (
                "# RTX 4090 vs RTX 5090 Comparison\n\n"
                "Per [NVIDIA RTX 4090](https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/), "
                "the RTX 4090 offers 24 GB memory and 450W TDP.\n"
                "Per [NVIDIA RTX 5090](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/), "
                "the RTX 5090 offers 32 GB memory and 575W TDP.\n"
            ),
            ["RTX 4090: 24 GB", "RTX 5090: 32 GB"],
            [],
        ),
        (
            "dr4bench0002",
            "technical",
            "Verify Python asyncio TaskGroup and SQLite WAL mode technical behavior",
            {
                1: [
                    {
                        "url": "https://docs.python.org/3/library/asyncio-task.html",
                        "title": "Python asyncio TaskGroup Docs",
                        "summary": "TaskGroup cancels remaining tasks if one raises an unhandled exception and raises ExceptionGroup.",
                        "round": 1,
                    },
                    {
                        "url": "https://www.sqlite.org/wal.html",
                        "title": "SQLite Write-Ahead Logging",
                        "summary": "WAL mode allows concurrent readers with a single writer using -wal and -shm files.",
                        "round": 1,
                    },
                ]
            },
            "## Draft\nTaskGroup groups tasks; SQLite WAL separates readers and writers.",
            (
                "# Technical Documentation Findings\n\n"
                "- [Python asyncio Docs](https://docs.python.org/3/library/asyncio-task.html): TaskGroup raises ExceptionGroup on child failure.\n"
                "- [SQLite WAL Docs](https://www.sqlite.org/wal.html): WAL permits concurrent readers alongside one writer.\n"
            ),
            [],
            [],
        ),
        (
            "dr4bench0003",
            "financial",
            "Synthetic portfolio sensitivity: compare US 10-Year Treasury yield benchmarks and duration impact",
            {
                1: [
                    {
                        "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
                        "title": "U.S. Department of the Treasury Daily Yield Curve",
                        "summary": "Public Treasury benchmark data lists 10-Year CMT yield at 4.25% and 2-Year CMT yield at 3.95%.",
                        "round": 1,
                    },
                    {
                        "url": "https://fred.stlouisfed.org/series/DGS10",
                        "title": "FRED DGS10 Series",
                        "summary": "Federal Reserve Economic Data confirms 10-Year Treasury Constant Maturity Rate historical series.",
                        "round": 1,
                    },
                ]
            },
            "## Draft\nPublic benchmark yields from Treasury.gov and FRED.",
            (
                "# Public Yield Benchmark & Synthetic Duration Analysis\n\n"
                "Using public rates from [U.S. Treasury](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) "
                "and [FRED DGS10](https://fred.stlouisfed.org/series/DGS10), a synthetic 7-year duration bond portfolio shifts ~7.0% per 100 bps rate move.\n"
            ),
            [],
            [],
        ),
    ],
)
async def test_dr4_three_benchmark_scenarios_end_to_end(
    tmp_path: Path,
    monkeypatch,
    scenario_id: str,
    category: str,
    question: str,
    rounds_plan: dict,
    draft_report: str,
    final_report: str,
    expected_verified: list[str],
    expected_unverified: list[str],
):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    monkeypatch.setattr("src.tools.research.DEEP_RESEARCH_DIR", tmp_path)

    researcher = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=2,
        min_rounds=1,
        max_time=300,
    )
    _configure_mock_pipeline(
        researcher,
        monkeypatch,
        category=category,
        rounds_plan=rounds_plan,
        draft_report=draft_report,
        final_report=final_report,
    )

    t0 = time.perf_counter()
    output = await researcher.research(question)
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.0
    assert researcher.verification_status in ("verified", "completed_with_gaps")
    for item in expected_verified:
        assert item in researcher.verification_summary["verified_facts"]
    assert researcher.verification_summary["unverified_citations"] == []

    handler = ResearchHandler()
    sid = f"rp-{scenario_id}"
    entry = {
        "query": question,
        "status": "done",
        "result": output,
        "raw_report": output,
        "stats": {"Model": "gemma4:31b", "Rounds": researcher.round_count, "URLs": len(researcher.urls_fetched)},
        "category": category,
        "started_at": time.time() - elapsed,
        "owner": "benchmark",
        "researcher": researcher,
    }
    handler._save_result(sid, entry)

    loaded = json.loads((tmp_path / f"{sid}.json").read_text(encoding="utf-8"))
    assert loaded["report"] == output
    assert loaded["model"] == "gemma4:31b"
    assert loaded["rounds"] == researcher.round_count and 1 <= loaded["rounds"] <= 2
    assert loaded["verification_summary"]["verification_status"] == researcher.verification_status

    read_res = await do_manage_research(json.dumps({"action": "read", "id": sid}), owner="benchmark")
    assert read_res["exit_code"] == 0
    assert question in read_res["output"]


@pytest.mark.asyncio
async def test_dr4_adversarial_1_searxng_unavailable_mid_research(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=3,
        min_rounds=1,
        max_time=120,
    )
    r.simple_research_mode = False
    r.max_empty_rounds = 2

    async def _plan_strat(q): return "Plan"
    async def _classify(q, p): return "technical"
    async def _plan(q, rep, rnd): return [ResearchAction("web_search", {"query": "test"})]
    async def _exec(actions, q):
        r._last_search_error = "ConnectionRefusedError: SearXNG :8080 down"
        return []

    monkeypatch.setattr(r, "_create_plan", _plan_strat)
    monkeypatch.setattr(r, "_classify_category", _classify)
    monkeypatch.setattr(r, "_plan_research_actions", _plan)
    monkeypatch.setattr(r, "_execute_research_actions", _exec)

    out = await r.research("Test SearXNG interruption")
    assert "**Search unavailable**" in out
    assert "SearXNG :8080 down" in out


@pytest.mark.asyncio
async def test_dr4_adversarial_2_llm_synthesis_timeout_fallback(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=1,
        min_rounds=1,
        max_time=120,
    )
    r.simple_research_mode = False

    async def _plan_strat(q): return "Plan"
    async def _classify(q, p): return "technical"
    async def _plan(q, rep, rnd): return [ResearchAction("web_search", {"query": "test"})]
    async def _exec(actions, q):
        u = "https://example.com/doc"
        r.urls_fetched.add(u)
        return [{"url": u, "title": "Doc", "summary": "Preserved finding text.", "round": 1}]
    async def _timeout_llm(*args, **kwargs):
        raise TimeoutError("ReadTimeout during synthesis")

    monkeypatch.setattr(r, "_create_plan", _plan_strat)
    monkeypatch.setattr(r, "_classify_category", _classify)
    monkeypatch.setattr(r, "_plan_research_actions", _plan)
    monkeypatch.setattr(r, "_execute_research_actions", _exec)
    monkeypatch.setattr(r, "_llm", _timeout_llm)

    out = await r.research("Test synthesis timeout")
    assert "Preserved finding text." in out


@pytest.mark.asyncio
async def test_dr4_adversarial_3_and_4_incorrect_specs_and_contradictory_sources(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=1,
        min_rounds=1,
        max_time=120,
    )
    _configure_mock_pipeline(
        r,
        monkeypatch,
        category="comparison",
        rounds_plan={
            1: [
                {
                    "url": "https://source-a.example.com/gpu",
                    "title": "Source A",
                    "summary": "RTX 5090 TDP is 500W and memory is 32 GB.",
                    "round": 1,
                },
                {
                    "url": "https://source-b.example.com/gpu",
                    "title": "Source B",
                    "summary": "RTX 5090 TDP is 575W and memory is 32 GB.",
                    "round": 1,
                },
            ]
        },
        draft_report="RTX 5090 has 32 GB and 575W; RTX 4090 has 32 GB.",
        final_report=(
            "# GPU Check\n"
            "- RTX 5090 has 32 GB and 575W.\n"
            "- RTX 4090 has 32 GB.\n"
            "- See [Fabricated](https://fake.example.invalid/spec).\n"
        ),
    )

    out = await r.research("Check contradictory and incorrect GPU specs")
    assert r.verification_status == "completed_with_gaps"
    assert "RTX 4090: 32 GB" in r.verification_summary["unsupported_claims"]
    assert any("RTX 5090 has conflicting W values" in c for c in r.verification_summary["conflicting_evidence"])
    assert len(r.verification_summary["unverified_citations"]) == 1
    assert "[Conflict]" in out
    assert "[Unverified]" in out
    assert "[Unverified Citation]" in out


@pytest.mark.asyncio
async def test_dr4_adversarial_5_verifier_exception_post_synthesis(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=1,
        min_rounds=1,
        max_time=120,
    )
    _configure_mock_pipeline(
        r,
        monkeypatch,
        category="technical",
        rounds_plan={1: [{"url": "https://example.com/a", "title": "A", "summary": "Fact A", "round": 1}]},
        draft_report="Draft A",
        final_report="# Surviving Report\n\nImportant findings.",
    )
    monkeypatch.setattr("src.deep_research.verify_report_evidence", lambda *a, **kw: (_ for _ in ()).throw(ValueError("bad regex state")))

    out = await r.research("Test post-synthesis verifier crash")
    assert "# Surviving Report" in out
    assert r.verification_status == "error"
    assert "**Verification Status:** `error`" in out


@pytest.mark.asyncio
async def test_dr4_adversarial_6_budget_exhaustion_stops_cleanly(monkeypatch):
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=5,
        min_rounds=1,
        max_time=1,
    )
    r.simple_research_mode = False

    async def _plan_strat(q): return "Plan"
    async def _classify(q, p): return "technical"
    async def _plan(q, rep, rnd): return [ResearchAction("web_search", {"query": "test"})]
    async def _exec(actions, q):
        r._start_time = time.time() - 10.0  # force wall-clock budget exhaustion
        u = "https://example.com/r1"
        r.urls_fetched.add(u)
        return [{"url": u, "title": "R1", "summary": "Round 1 data", "round": 1}]
    async def _synth(q, f, rep): return "## Partial Round 1 Report\nRound 1 data"
    async def _final(q, rep): return rep

    monkeypatch.setattr(r, "_create_plan", _plan_strat)
    monkeypatch.setattr(r, "_classify_category", _classify)
    monkeypatch.setattr(r, "_plan_research_actions", _plan)
    monkeypatch.setattr(r, "_execute_research_actions", _exec)
    monkeypatch.setattr(r, "_synthesize", _synth)
    monkeypatch.setattr(r, "_final_report", _final)

    out = await r.research("Test budget exhaustion")
    assert len(r.findings) == 1
    assert r.round_count == 2  # broke immediately at top of round 2 before search
    assert "Partial Round 1 Report" in out
