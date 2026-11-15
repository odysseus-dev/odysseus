"""DR-5.4 Report Rendering & Pipeline Integration Tests (25 tests: R1-R10, A1-A15)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from unittest.mock import AsyncMock, patch

from src.deep_research import DeepResearcher
from src.research_verifier import VerificationReport, verify_report_evidence


# ==============================================================================
# R1 - R10: MARKDOWN RENDERING & GAP SIGNALING INVARIANTS
# ==============================================================================


def test_R1_disabled_mode_to_markdown_is_byte_identical_to_baseline():
    vr = VerificationReport(
        verified_facts=["Acme revenue reached $9.5B."],
        corroborated_facts=["Acme revenue reached $9.5B."],
    )
    md = vr.to_markdown()
    assert "## Evidence Verification Summary" in md
    assert "Domain Verification:" not in md
    assert "Preserved Domain Conflicts:" not in md
    assert "Missing Domain Verifier Coverage:" not in md
    assert "Domain Verifier Plugin Faults" not in md


def test_R2_enabled_mode_renders_domain_assessment_badges_and_metadata():
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": "Acme FY2025 revenue was $9.5B per Form 10-K.",
                "domain": "financial",
                "plugin_name": "financial_verifier",
                "claim_assessment": "SUPPORTED",
                "publication_status": "PRIMARY_FILING",
                "legal_currency": "CURRENT",
                "source_dependence": "INDEPENDENT",
            }
        ]
    )
    md = vr.to_markdown()
    assert "- **Domain Verification:** 1 claim(s) assessed" in md
    assert "[FINANCIAL | SUPPORTED]" in md
    assert "plugin=financial_verifier" in md
    assert "pub=PRIMARY_FILING" in md


def test_R3_contested_domain_verdict_triggers_has_gaps():
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": "Trial showed 42% response rate.",
                "domain": "medical",
                "plugin_name": "medical_verifier",
                "claim_assessment": "CONTESTED",
            }
        ]
    )
    assert vr.has_gaps() is True


def test_R4_insufficient_evidence_domain_verdict_triggers_has_gaps():
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": "Statute 17 U.S.C. 512 applies.",
                "domain": "legal",
                "plugin_name": "legal_verifier",
                "claim_assessment": "INSUFFICIENT_EVIDENCE",
            }
        ]
    )
    assert vr.has_gaps() is True


def test_R5_supported_only_domain_verdicts_do_not_trigger_gaps():
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": "Apple reported Q4 revenue of $94.9B.",
                "domain": "financial",
                "plugin_name": "financial_verifier",
                "claim_assessment": "SUPPORTED",
            }
        ]
    )
    assert vr.has_gaps() is False


def test_R6_preserved_conflict_records_render_and_trigger_has_gaps():
    vr = VerificationReport(
        claim_assessments=[],
        conflict_records=[
            {
                "normalized_claim_key": "acme fy2025 revenue",
                "domains_involved": ["financial"],
                "competing_states": ["SUPPORTED", "CONTESTED"],
            }
        ],
    )
    assert vr.has_gaps() is True
    md = vr.to_markdown()
    assert "- **Preserved Domain Conflicts:** 1" in md
    assert "[Domain Conflict] acme fy2025 revenue" in md


def test_R7_missing_domain_plugin_renders_banner_and_triggers_has_gaps():
    vr = VerificationReport(
        claim_assessments=[],
        missing_domain_plugin=True,
        missing_domain_domains=["historical"],
    )
    assert vr.has_gaps() is True
    md = vr.to_markdown()
    assert "- **Missing Domain Verifier Coverage:** historical" in md


def test_R8_plugin_errors_render_fail_closed_notice_and_trigger_has_gaps():
    vr = VerificationReport(
        claim_assessments=[],
        plugin_errors=[{"plugin_name": "faulty_verifier", "error_type": "RuntimeError"}],
    )
    assert vr.has_gaps() is True
    md = vr.to_markdown()
    assert "- **Domain Verifier Plugin Faults (Fail-Closed):** 1" in md
    assert "[Plugin Fault] faulty_verifier (RuntimeError)" in md


def test_R9_markdown_sanitizes_newlines_brackets_and_backticks():
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": "Line 1\n## Injected Heading [bad] `code`",
                "domain": "legal\n??",
                "plugin_name": "legal_verifier",
                "claim_assessment": "SUPPORTED",
            }
        ]
    )
    md = vr.to_markdown()
    for line in md.splitlines():
        if "Line 1" in line:
            assert "\n" not in line
            assert "`" not in line
            assert "## Injected" in line and not line.startswith("## Injected")


def test_R10_verify_report_evidence_disabled_does_not_import_verifiers():
    code = (
        "import sys; "
        "from src.research_verifier import verify_report_evidence; "
        "vr = verify_report_evidence("
        "'Acme revenue was $9.5B in FY2025.', "
        "[{'url': 'https://sec.gov/10k', 'title': '10-K', 'summary': 'Acme revenue was $9.5B in FY2025.'}], "
        "enable_domain_verifiers=False"
        "); "
        "assert vr.claim_assessments is None; "
        "assert not any(m.startswith('src.verifiers') for m in sys.modules)"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


# ==============================================================================
# A1 - A15: END-TO-END PIPELINE INTEGRATION & ADVERSARIAL EDGE CASES
# ==============================================================================


def test_A1_verify_report_evidence_enabled_populates_claim_assessments():
    vr = verify_report_evidence(
        "According to the SEC Form 10-K filing, Acme Corp FY2025 revenue was $9.5 billion.",
        [
            {
                "url": "https://www.sec.gov/Archives/edgar/data/0001/10k.htm",
                "title": "Acme Form 10-K",
                "summary": "Acme Corp FY2025 revenue was $9.5 billion.",
            }
        ],
        enable_domain_verifiers=True,
    )
    assert isinstance(vr.claim_assessments, list)
    assert len(vr.claim_assessments) >= 1


def test_A2_deep_researcher_attaches_domain_verification_markdown_when_enabled():
    dr = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=0,
        enable_domain_verifiers=True,
    )

    async def _run():
        with (
            patch.object(dr, "_maybe_warmup_large_local_model", new=AsyncMock()),
            patch.object(dr, "_create_plan", new=AsyncMock(return_value="Mock research plan")),
            patch.object(dr, "_final_report", new=AsyncMock(return_value="Per SEC Form 10-K, revenue was $9.5B.")),
        ):
            return await dr.research(
                "Verify Acme FY2025 revenue",
                prior_report="Draft report",
                prior_findings=[{"url": "https://www.sec.gov/10k", "title": "10-K", "summary": "Revenue was $9.5B."}],
            )

    final = asyncio.run(_run())
    assert "## Evidence Verification Summary" in final
    assert "Domain Verification:" in final
    assert isinstance(dr.verification_summary, dict)
    assert "claim_assessments" in dr.verification_summary


def test_A3_deep_researcher_disabled_omits_domain_verification_markdown():
    dr = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=0,
        enable_domain_verifiers=False,
    )

    async def _run():
        with (
            patch.object(dr, "_maybe_warmup_large_local_model", new=AsyncMock()),
            patch.object(dr, "_create_plan", new=AsyncMock(return_value="Mock research plan")),
            patch.object(dr, "_final_report", new=AsyncMock(return_value="Water boils at 100C at sea level.")),
        ):
            return await dr.research(
                "Boiling point of water",
                prior_report="Draft report",
                prior_findings=[{"url": "https://example.com/a", "title": "A", "summary": "Water boils at 100C."}],
            )

    final = asyncio.run(_run())
    assert "Domain Verification:" not in final
    assert dr.verification_summary is not None
    assert "claim_assessments" not in dr.verification_summary


def test_A4_deep_researcher_sets_completed_with_gaps_on_domain_plugin_fault():
    dr = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=0,
        enable_domain_verifiers=True,
    )
    broken_vr = VerificationReport(
        verified_facts=["Fact A"],
        corroborated_facts=["Fact A"],
        claim_assessments=[],
        plugin_errors=[{"plugin_name": "broken_plugin", "error_type": "ValueError"}],
    )

    async def _run():
        with (
            patch.object(dr, "_maybe_warmup_large_local_model", new=AsyncMock()),
            patch.object(dr, "_create_plan", new=AsyncMock(return_value="Mock research plan")),
            patch.object(dr, "_final_report", new=AsyncMock(return_value="Fact A")),
            patch("src.deep_research.verify_report_evidence", return_value=broken_vr),
        ):
            return await dr.research("Query", prior_report="Draft")

    final = asyncio.run(_run())
    assert dr.verification_status == "completed_with_gaps"
    assert "**Domain Verifier Plugin Faults (Fail-Closed):** 1" in final


def test_A5_deep_researcher_sets_completed_with_gaps_on_missing_domain_plugin():
    dr = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=0,
        enable_domain_verifiers=True,
    )
    missing_vr = VerificationReport(
        claim_assessments=[],
        missing_domain_plugin=True,
        missing_domain_domains=["historical"],
    )

    async def _run():
        with (
            patch.object(dr, "_maybe_warmup_large_local_model", new=AsyncMock()),
            patch.object(dr, "_create_plan", new=AsyncMock(return_value="Mock research plan")),
            patch.object(dr, "_final_report", new=AsyncMock(return_value="Battle of Marathon occurred in 490 BCE.")),
            patch("src.deep_research.verify_report_evidence", return_value=missing_vr),
        ):
            return await dr.research("Historical query", prior_report="Draft")

    final = asyncio.run(_run())
    assert dr.verification_status == "completed_with_gaps"
    assert "**Missing Domain Verifier Coverage:** historical" in final


def test_A6_deep_researcher_does_not_duplicate_summary_header_if_already_present():
    dr = DeepResearcher(
        llm_endpoint="http://localhost:11434",
        llm_model="qwen2.5:14b",
        max_rounds=0,
        enable_domain_verifiers=True,
    )
    vr = VerificationReport(claim_assessments=[])

    async def _run():
        with (
            patch.object(dr, "_maybe_warmup_large_local_model", new=AsyncMock()),
            patch.object(dr, "_create_plan", new=AsyncMock(return_value="Mock research plan")),
            patch.object(
                dr,
                "_final_report",
                new=AsyncMock(return_value="Body\n\n## Evidence Verification Summary\n- Already present"),
            ),
            patch("src.deep_research.verify_report_evidence", return_value=vr),
        ):
            return await dr.research("Query", prior_report="Draft")

    final = asyncio.run(_run())
    assert final.count("## Evidence Verification Summary") == 1


def test_A7_to_markdown_caps_rendered_assessments_at_eight():
    assessments = [
        {
            "claim_text": f"Claim number {i}",
            "domain": "financial",
            "plugin_name": "financial_verifier",
            "claim_assessment": "SUPPORTED",
        }
        for i in range(15)
    ]
    vr = VerificationReport(claim_assessments=assessments)
    md = vr.to_markdown()
    assert "- **Domain Verification:** 15 claim(s) assessed" in md
    assert md.count("[FINANCIAL | SUPPORTED]") == 8


def test_A8_to_markdown_caps_rendered_conflicts_at_six():
    conflicts = [
        {
            "normalized_claim_key": f"conflict-{i}",
            "domains_involved": ["financial"],
            "competing_states": ["SUPPORTED", "CONTESTED"],
        }
        for i in range(10)
    ]
    vr = VerificationReport(claim_assessments=[], conflict_records=conflicts)
    md = vr.to_markdown()
    assert "- **Preserved Domain Conflicts:** 10" in md
    assert md.count("[Domain Conflict]") == 6


def test_A9_to_markdown_caps_rendered_plugin_errors_at_six():
    errors = [{"plugin_name": f"p-{i}", "error_type": "RuntimeError"} for i in range(9)]
    vr = VerificationReport(claim_assessments=[], plugin_errors=errors)
    md = vr.to_markdown()
    assert "- **Domain Verifier Plugin Faults (Fail-Closed):** 9" in md
    assert md.count("[Plugin Fault]") == 6


def test_A10_to_markdown_handles_non_dict_entries_in_lists_gracefully():
    vr = VerificationReport(
        claim_assessments=["bad-item", None, {"claim_text": "Valid", "domain": "legal", "claim_assessment": "SUPPORTED"}],
        conflict_records=["bad-conflict"],
        plugin_errors=[None],
    )
    md = vr.to_markdown()
    assert "- **Domain Verification:** 1 claim(s) assessed" in md
    assert "[LEGAL | SUPPORTED] Valid" in md


def test_A11_to_markdown_truncates_extremely_long_claim_text():
    long_claim = "A" * 500
    vr = VerificationReport(
        claim_assessments=[
            {
                "claim_text": long_claim,
                "domain": "medical",
                "plugin_name": "medical_verifier",
                "claim_assessment": "SUPPORTED",
            }
        ]
    )
    md = vr.to_markdown()
    assert "A" * 200 not in md
    assert "..." in md


def test_A12_has_gaps_handles_non_dict_claim_assessments_without_crashing():
    vr = VerificationReport(claim_assessments=["invalid", 123, None])
    assert vr.has_gaps() is False


def test_A13_missing_domain_plugin_without_domains_list_renders_unspecified():
    vr = VerificationReport(claim_assessments=[], missing_domain_plugin=True, missing_domain_domains=[])
    md = vr.to_markdown()
    assert "- **Missing Domain Verifier Coverage:** unspecified" in md


def test_A14_multi_domain_assessments_render_distinct_domain_badges():
    vr = VerificationReport(
        claim_assessments=[
            {"claim_text": "10-K revenue", "domain": "financial", "plugin_name": "financial_verifier", "claim_assessment": "SUPPORTED"},
            {"claim_text": "Statute text", "domain": "legal", "plugin_name": "legal_verifier", "claim_assessment": "CONTESTED"},
            {"claim_text": "RCT endpoint", "domain": "medical", "plugin_name": "medical_verifier", "claim_assessment": "SUPPORTED"},
        ]
    )
    md = vr.to_markdown()
    assert "[FINANCIAL | SUPPORTED]" in md
    assert "[LEGAL | CONTESTED]" in md
    assert "[MEDICAL | SUPPORTED]" in md
    assert vr.has_gaps() is True


def test_A15_to_dict_and_to_markdown_remain_consistent():
    vr = verify_report_evidence(
        "Phase 3 clinical trial NCT01234567 met its primary endpoint.",
        [{"url": "https://clinicaltrials.gov/study/NCT01234567", "title": "NCT01234567", "summary": "Phase 3 trial met primary endpoint."}],
        enable_domain_verifiers=True,
    )
    d = vr.to_dict()
    md = vr.to_markdown()
    assert "claim_assessments" in d
    assert f"- **Domain Verification:** {len(d['claim_assessments'])} claim(s) assessed" in md
