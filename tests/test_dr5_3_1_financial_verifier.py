"""DR-5.3.1 Financial Domain Verifier Adversarial & Isolation Tests (8 tests)."""

from __future__ import annotations

import json
import subprocess
import sys

from src.research_sources import LegalCurrencyStatus
from src.research_verifier import verify_report_evidence
from src.verifiers.base import build_default_registry


def test_dr5_3_1_f1_primary_sec_and_treasury_corroboration():
    findings = [
        {
            "url": "https://www.sec.gov/Archives/edgar/data/0000320193/10k-2025.htm",
            "title": "Acme Corp FY2025 Form 10-K",
            "summary": "Acme Corp FY2025 revenue was $94.9 billion on a GAAP basis.",
            "publication_date": "2025-11-01",
        },
        {
            "url": "https://www.reuters.com/markets/companies/acme-fy2025-results/",
            "title": "Reuters: Acme Corp FY2025 Results",
            "summary": "Independent filing review confirms Acme Corp FY2025 revenue was $94.9 billion.",
            "publication_date": "2025-11-02",
        },
    ]
    vr = verify_report_evidence(
        "- Acme Corp FY2025 revenue was $94.9 billion.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    d = vr.to_dict()
    assert len(d["claim_assessments"]) == 1
    ca = d["claim_assessments"][0]
    assert ca["plugin_name"] == "financial_verifier"
    assert ca["claim_assessment"] == "SUPPORTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_VERIFIED.value
    assert ca["evidence_classification"] == "PRIMARY_CORROBORATED"
    assert "%" not in json.dumps(ca)


def test_dr5_3_1_f2_prevents_cross_entity_and_cross_quarter_conflation():
    findings = [
        {
            "url": "https://www.sec.gov/Archives/edgar/data/111/q2.htm",
            "title": "Alpha Corp Q2 2026 10-Q",
            "summary": "Alpha Corp Q2 2026 revenue was $12.4 billion.",
            "publication_date": "2026-08-01",
        },
        {
            "url": "https://www.sec.gov/Archives/edgar/data/222/q3.htm",
            "title": "Beta Corp Q3 2026 10-Q",
            "summary": "Beta Corp Q3 2026 revenue was $18.1 billion.",
            "publication_date": "2026-11-01",
        },
    ]
    vr = verify_report_evidence(
        "- Alpha Corp Q3 2026 revenue was $18.1 billion.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"


def test_dr5_3_1_f3_gaap_vs_non_gaap_mismatch_flagged():
    findings = [
        {
            "url": "https://www.sec.gov/Archives/edgar/data/333/8k.htm",
            "title": "Northstar Inc Q3 2026 Earnings Release",
            "summary": "Northstar Inc Q3 2026 Non-GAAP EPS was $2.15 per diluted share.",
            "publication_date": "2026-10-28",
        }
    ]
    vr = verify_report_evidence(
        "- Northstar Inc Q3 2026 GAAP EPS was $2.15.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert "NON_GAAP_VS_GAAP" in ca["rationale"]["directness"]


def test_dr5_3_1_f4_undated_yield_fails_closed_on_currency():
    findings = [
        {
            "url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
            "title": "Daily Treasury Par Yield Curve Rates",
            "summary": "US 10-Year Treasury yield stands at 4.25%.",
        }
    ]
    vr = verify_report_evidence(
        "- US 10-Year Treasury yield stands at 4.25%.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "SUPPORTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value


def test_dr5_3_1_f5_contradictory_financial_metrics_preserve_conflict():
    findings = [
        {
            "url": "https://www.sec.gov/Archives/edgar/data/444/10k.htm",
            "title": "Orion Corp FY2025 10-K",
            "summary": "Orion Corp FY2025 revenue was $5.1 billion.",
            "publication_date": "2026-02-15",
        },
        {
            "url": "https://www.wsj.com/articles/orion-fy2025-restatement",
            "title": "WSJ: Orion FY2025 Filing",
            "summary": "Orion Corp FY2025 revenue was $6.3 billion.",
            "publication_date": "2026-02-16",
        },
    ]
    vr = verify_report_evidence(
        "- Orion Corp FY2025 revenue was $5.1 billion.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    d = vr.to_dict()
    ca = d["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert len(ca["conflicting_passages"]) >= 1
    assert len(d["conflict_records"]) == 1
    assert d["conflict_records"][0]["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS"


def test_dr5_3_1_f6_unverified_blogs_never_corroborate_financial_claims():
    findings = [
        {
            "url": "https://stock-rumor-blog-1.example.net/post",
            "title": "Rumor Blog 1",
            "summary": "Vanguard Tech FY2026 revenue was $42.0 billion.",
        },
        {
            "url": "https://stock-rumor-blog-2.example.org/post",
            "title": "Rumor Blog 2",
            "summary": "Vanguard Tech FY2026 revenue was $42.0 billion.",
        },
    ]
    vr = verify_report_evidence(
        "- Vanguard Tech FY2026 revenue was $42.0 billion.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert ca["evidence_classification"] != "PRIMARY_CORROBORATED"


def test_dr5_3_1_f7_sanitizes_account_routing_secrets_while_preserving_financial_numbers():
    findings = [
        {
            "url": "https://www.sec.gov/Archives/edgar/data/555/10q.htm",
            "title": "Summit Bank Q1 2026 10-Q",
            "summary": "Summit Bank Q1 2026 revenue was $14.8B (routing number 021000021, account number 123456789012).",
            "publication_date": "2026-05-10",
        }
    ]
    vr = verify_report_evidence(
        "- Summit Bank Q1 2026 revenue was $14.8B.",
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "SUPPORTED"
    excerpt = ca["supporting_passages"][0]["excerpt"]
    assert "$14.8B" in excerpt
    assert "021000021" not in excerpt
    assert "123456789012" not in excerpt


def test_dr5_3_1_f8_cold_start_isolation_when_disabled():
    code = (
        "import sys\n"
        "from src.research_verifier import verify_report_evidence\n"
        "vr = verify_report_evidence('- Acme Corp FY2025 revenue was $94.9 billion.', [{'url': 'https://www.sec.gov/10k.htm', 'title': '10-K', 'summary': 'Acme Corp FY2025 revenue was $94.9 billion.'}], enable_domain_verifiers=False, category='financial')\n"
        "assert 'src.verifiers.financial' not in sys.modules\n"
        "assert 'src.verifiers' not in sys.modules\n"
        "assert 'claim_assessments' not in vr.to_dict()\n"
        "print('DR5_3_1_F8_COLD_START_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_3_1_F8_COLD_START_OK" in proc.stdout
