"""DR-3.1 full-loop integration & verification resilience tests for DeepResearcher."""

from __future__ import annotations

import pytest

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchAction


def _make_researcher(*, max_rounds: int = 2) -> DeepResearcher:
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="gemma4:31b",
        max_rounds=max_rounds,
        min_rounds=1,
        max_time=120,
    )
    r.simple_research_mode = False
    return r


@pytest.mark.asyncio
async def test_full_loop_runs_mid_and_final_verification_and_triggers_followup(monkeypatch):
    researcher = _make_researcher(max_rounds=2)
    executed_queries: list[str] = []

    async def _fake_plan_strategy(question: str) -> str:
        return "Compare RTX 4090 and RTX 5090 official specs."

    async def _fake_classify(question: str, plan: str) -> str:
        return "comparison"

    async def _fake_plan(question: str, report: str, round_num: int):
        return [ResearchAction("web_search", {"query": f"round {round_num} gpu specs"})]

    async def _fake_exec(actions, question: str):
        for act in actions:
            q = act.args.get("query", "")
            executed_queries.append(q)
            if "RTX 4090 official specifications" in q:
                url = "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/"
                researcher.urls_fetched.add(url)
                return [
                    {
                        "url": url,
                        "title": "NVIDIA RTX 4090 Official Specs",
                        "summary": "The NVIDIA RTX 4090 has 24 GB GDDR6X memory and 450W TDP.",
                        "round": 2,
                    }
                ]
        url = "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/"
        researcher.urls_fetched.add(url)
        return [
            {
                "url": url,
                "title": "NVIDIA RTX 5090 Official Specs",
                "summary": "The NVIDIA RTX 5090 has 32 GB GDDR7 memory and 575W TDP.",
                "round": 1,
            }
        ]

    async def _fake_synthesize(question: str, findings, current_report: str) -> str:
        return (
            "## GPU Comparison\n"
            "- RTX 5090 features 32 GB VRAM and 575W TDP.\n"
            "- RTX 4090 features 24 GB VRAM and 450W TDP.\n"
        )

    async def _fake_should_stop(question: str, report: str, round_num: int) -> bool:
        return True

    async def _fake_final_report(question: str, report: str) -> str:
        return (
            "# Final GPU Comparison\n\n"
            "- The RTX 5090 has 32 GB GDDR7 memory and 575W TDP.\n"
            "- The RTX 4090 has 24 GB GDDR6X memory and 450W TDP.\n"
        )

    monkeypatch.setattr(researcher, "_create_plan", _fake_plan_strategy)
    monkeypatch.setattr(researcher, "_classify_category", _fake_classify)
    monkeypatch.setattr(researcher, "_plan_research_actions", _fake_plan)
    monkeypatch.setattr(researcher, "_execute_research_actions", _fake_exec)
    monkeypatch.setattr(researcher, "_synthesize", _fake_synthesize)
    monkeypatch.setattr(researcher, "_should_stop", _fake_should_stop)
    monkeypatch.setattr(researcher, "_final_report", _fake_final_report)

    final_report = await researcher.research("Compare RTX 4090 and RTX 5090 specs")

    assert any("RTX 4090 official specifications" in q for q in executed_queries)
    assert getattr(researcher, "verification_status", None) == "verified"
    assert isinstance(researcher.verification_summary, dict)
    assert researcher.verification_summary["verification_status"] == "verified"
    assert "RTX 5090: 32 GB" in researcher.verification_summary["verified_facts"]
    assert "RTX 4090: 24 GB" in researcher.verification_summary["verified_facts"]
    assert "## Evidence Verification Summary" in final_report
    assert "[Verified] RTX 4090: 24 GB" in final_report


@pytest.mark.asyncio
async def test_verifier_exception_preserves_report_and_discloses_error(monkeypatch):
    researcher = _make_researcher(max_rounds=1)

    async def _fake_plan_strategy(question: str) -> str:
        return "Plan"

    async def _fake_classify(question: str, plan: str) -> str:
        return "factcheck"

    async def _fake_plan(question: str, report: str, round_num: int):
        return [ResearchAction("web_search", {"query": "test query"})]

    async def _fake_exec(actions, question: str):
        url = "https://example.com/specs"
        researcher.urls_fetched.add(url)
        return [{"url": url, "title": "Specs", "summary": "RTX 5090 has 32 GB.", "round": 1}]

    async def _fake_synthesize(question: str, findings, current_report: str) -> str:
        return "## Synthesized Draft\nRTX 5090 has 32 GB."

    async def _fake_should_stop(question: str, report: str, round_num: int) -> bool:
        return True

    async def _fake_final_report(question: str, report: str) -> str:
        return "# Completed Research Report\n\nDetailed findings preserved intact."

    def _boom_verifier(*args, **kwargs):
        raise RuntimeError("simulated verifier failure")

    monkeypatch.setattr(researcher, "_create_plan", _fake_plan_strategy)
    monkeypatch.setattr(researcher, "_classify_category", _fake_classify)
    monkeypatch.setattr(researcher, "_plan_research_actions", _fake_plan)
    monkeypatch.setattr(researcher, "_execute_research_actions", _fake_exec)
    monkeypatch.setattr(researcher, "_synthesize", _fake_synthesize)
    monkeypatch.setattr(researcher, "_should_stop", _fake_should_stop)
    monkeypatch.setattr(researcher, "_final_report", _fake_final_report)
    monkeypatch.setattr("src.deep_research.verify_report_evidence", _boom_verifier)

    final_report = await researcher.research("Test verifier resilience")

    assert "# Completed Research Report" in final_report
    assert "Detailed findings preserved intact." in final_report
    assert researcher.verification_status == "error"
    assert researcher.verification_summary["verification_status"] == "error"
    assert "RuntimeError" in researcher.verification_summary["error"]
    assert "## Evidence Verification Summary" in final_report
    assert "**Verification Status:** `error`" in final_report
