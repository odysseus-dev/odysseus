"""DR-5.2 Modular Verifier Registry, Four-Axis ClaimAssessment & Cold-Start Isolation Tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.deep_research import DeepResearcher, ResearchAction
from src.research_verifier import verify_report_evidence
from src.verifiers.base import (
    ClaimAssessment,
    VerifierRegistry,
    build_default_registry,
)


def test_dr5_2_default_flag_is_false_and_matches_dr4_2_dict_schema_identically():
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
            "title": "NVIDIA RTX 5090 Specs",
            "summary": "NVIDIA RTX 5090 features 32 GB GDDR7 memory and 575W TDP.",
        },
        {
            "url": "https://www.techpowerup.com/review/nvidia-geforce-rtx-5090/",
            "title": "TechPowerUp RTX 5090 Review",
            "summary": "Tested on our GPU test bench using power analyzer: RTX 5090 has 32 GB VRAM and 575W TDP.",
        },
    ]
    report = "- The RTX 5090 features 32 GB VRAM and 575W TDP."

    r = DeepResearcher(llm_endpoint="http://127.0.0.1:11434/v1", llm_model="gemma4:31b")
    assert r.enable_domain_verifiers is False

    vr_default = verify_report_evidence(report, findings)
    d = vr_default.to_dict()
    assert set(d.keys()) == {
        "verified_facts",
        "corroborated_facts",
        "single_source_claims",
        "conflicting_evidence",
        "unsupported_claims",
        "unverified_citations",
        "followup_queries",
    }
    assert vr_default.claim_assessments is None
    assert vr_default.provenance_summary is None
    assert vr_default.provenance_edges is None


def test_dr5_2_registry_and_four_axis_claim_assessment_when_explicitly_enabled():
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
            "title": "NVIDIA RTX 5090 Specs",
            "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP.",
        },
        {
            "url": "https://www.techpowerup.com/review/nvidia-geforce-rtx-5090/",
            "title": "TechPowerUp RTX 5090 Review",
            "summary": "Tested on our GPU test bench using power analyzer and driver version 570.12: RTX 5090 has 32 GB VRAM.",
        },
        {
            "url": "https://random-blog.example.net/rtx-5090-summary",
            "title": "Blog Summary",
            "summary": "Rumored RTX 5090 clock speed is 2.9 GHz.",
        },
    ]
    report = "- RTX 5090 has 32 GB VRAM and 450W TDP."

    reg = build_default_registry()
    assert "hardware_baseline" in reg.list_plugins()

    vr = verify_report_evidence(
        report,
        findings,
        enable_domain_verifiers=True,
        category="hardware",
        registry=reg,
    )
    d = vr.to_dict()
    assert "claim_assessments" in d
    assert "provenance_summary" in d
    assert d["provenance_summary"]["verified_independent_origin_count"] == 2

    by_claim = {c["claim_text"]: c for c in d["claim_assessments"]}
    c_32gb = by_claim["RTX 5090: 32 GB"]
    assert c_32gb["claim_assessment"] == "SUPPORTED"
    assert "publication_status" in c_32gb
    assert "legal_currency" in c_32gb
    assert c_32gb["source_dependence"] == "INDEPENDENT"
    assert c_32gb["evidence_classification"] == "PRIMARY_CORROBORATED"
    assert len(c_32gb["supporting_passages"]) == 2
    assert set(c_32gb["rationale"].keys()) == {
        "source_authority",
        "independence",
        "directness",
        "contradictions",
        "evidence_completeness",
    }
    assert "%" not in json.dumps(d["claim_assessments"])

    c_450w = by_claim["RTX 5090: 450W"]
    assert c_450w["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert c_450w["evidence_classification"] == "UNVERIFIED_OR_UNSUPPORTED"


def test_dr5_2_custom_plugin_registration_and_category_routing():
    class DummyFinancePlugin:
        name = "dummy_finance"
        domain = "financial"

        def supports_category(self, category: str) -> bool:
            return category == "financial"

        def verify(self, report_text, assessments, edges, provenance_summary, baseline_report):
            return [
                ClaimAssessment(
                    claim_text="10Y Yield: 4.25%",
                    domain="financial",
                    claim_assessment="SUPPORTED",
                    publication_status="NOT_APPLICABLE",
                    legal_currency="NOT_APPLICABLE",
                    source_dependence="INDEPENDENT",
                    evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
                    authority_scope_note="Official Treasury data.",
                    rationale={
                        "source_authority": "PRIMARY_INSTITUTIONAL",
                        "independence": "1 primary source",
                        "directness": "DIRECT",
                        "contradictions": "None",
                        "evidence_completeness": "Complete",
                    },
                )
            ]

    reg = VerifierRegistry()
    reg.register(DummyFinancePlugin())
    assert [p.name for p in reg.get_applicable("financial")] == ["dummy_finance"]
    assert reg.get_applicable("hardware") == []


def test_dr5_2_genuine_subprocess_cold_start_isolation():
    """Fresh Python subprocess with default enable_domain_verifiers=False must never import DR-5 modules."""
    code = (
        "import sys; "
        "from src.deep_research import DeepResearcher; "
        "from src.research_verifier import verify_report_evidence; "
        "r = DeepResearcher(llm_endpoint='http://127.0.0.1:11434/v1', llm_model='gemma4:31b'); "
        "assert r.enable_domain_verifiers is False; "
        "vr = verify_report_evidence('- RTX 5090 has 32 GB VRAM.', [{'url': 'https://www.nvidia.com/rtx-5090', 'title': 'NVIDIA', 'summary': 'RTX 5090 has 32 GB VRAM.'}]); "
        "assert 'src.research_sources' not in sys.modules, 'src.research_sources imported when disabled!'; "
        "assert 'src.verifiers' not in sys.modules, 'src.verifiers imported when disabled!'; "
        "assert 'src.verifiers.base' not in sys.modules, 'src.verifiers.base imported when disabled!'; "
        "assert 'RTX 5090: 32 GB' in vr.verified_facts; "
        "print('DR5_2_COLD_START_ISOLATION_OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_2_COLD_START_ISOLATION_OK" in proc.stdout


@pytest.mark.asyncio
async def test_dr5_2_lockstep_behavioral_equivalence_with_dr4_2_loop(tmp_path: Path, monkeypatch):
    """Verify full DeepResearcher loop with enable_domain_verifiers=False matches DR-4.2 lockstep."""
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    monkeypatch.setattr("src.tools.research.DEEP_RESEARCH_DIR", tmp_path)

    async def _run_once(flag: bool):
        researcher = DeepResearcher(
            llm_endpoint="http://127.0.0.1:11434/v1",
            llm_model="gemma4:31b",
            max_rounds=2,
            min_rounds=1,
            max_time=120,
            enable_domain_verifiers=flag,
        )

        async def _plan_strategy(q: str) -> str:
            return "Compare RTX 4090 and RTX 5090."

        async def _classify(q: str, p: str) -> str:
            return "comparison"

        async def _plan_actions(q: str, rep: str, rnd: int):
            return [ResearchAction("web_search", {"query": f"round {rnd}"})]

        async def _exec(actions, q: str):
            url = "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/"
            researcher.urls_fetched.add(url)
            return [{
                "url": url,
                "title": "NVIDIA RTX 5090",
                "summary": "NVIDIA RTX 5090 has 32 GB GDDR7 and 575W TDP.",
                "round": 1,
            }]

        async def _synth(q: str, f, cur: str) -> str:
            return "- RTX 5090 has 32 GB VRAM and 575W TDP.\n- RTX 4090 has 24 GB VRAM."

        async def _stop(q: str, rep: str, rnd: int) -> bool:
            return True

        async def _final(q: str, rep: str) -> str:
            return "# Summary\n- RTX 5090 has 32 GB VRAM and 575W TDP.\n- RTX 4090 has 24 GB VRAM."

        monkeypatch.setattr(researcher, "_create_plan", _plan_strategy)
        monkeypatch.setattr(researcher, "_classify_category", _classify)
        monkeypatch.setattr(researcher, "_plan_research_actions", _plan_actions)
        monkeypatch.setattr(researcher, "_execute_research_actions", _exec)
        monkeypatch.setattr(researcher, "_synthesize", _synth)
        monkeypatch.setattr(researcher, "_should_stop", _stop)
        monkeypatch.setattr(researcher, "_final_report", _final)

        report_out = await researcher.research("Compare RTX 4090 and RTX 5090")
        return report_out, researcher.verification_status, researcher.verification_summary

    rep_a, status_a, sum_a = await _run_once(False)
    rep_b, status_b, sum_b = await _run_once(False)
    assert rep_a == rep_b
    assert status_a == status_b
    assert sum_a == sum_b
    assert "claim_assessments" not in sum_a
    assert "provenance_summary" not in sum_a
