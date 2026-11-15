"""DR-5.3.2 Legal & Regulatory Domain Verifier Adversarial & Isolation Tests (L1-L12)."""

from __future__ import annotations

import subprocess
import sys

from src.research_sources import LegalCurrencyStatus
from src.research_verifier import verify_report_evidence
from src.verifiers.base import build_default_registry


def test_dr5_3_2_l1_federal_vs_state_jurisdiction_isolation():
    findings = [
        {
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=1798.100",
            "title": "California Civil Code Section 1798.100",
            "summary": "Under California law, Section 1798.100 requires consumer data disclosure.",
            "publication_date": "2026-01-01",
        }
    ]
    vr = verify_report_evidence(
        "- Under Texas law, Section 1798.100 requires consumer data disclosure.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"


def test_dr5_3_2_l2_joint_jurisdiction_and_effective_date_version_binding():
    findings = [
        {
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=1798.150",
            "title": "California Civil Code Section 1798.150 (2026 Amended Text)",
            "summary": "California Section 1798.150 was amended effective January 1, 2026 to set statutory damages at $50,000.",
            "publication_date": "2026-01-02",
        }
    ]
    vr = verify_report_evidence(
        "- California Section 1798.150 sets statutory damages at $10,000 in 2023.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.AMENDED_OR_SUPERSEDED.value


def test_dr5_3_2_l3_repealed_or_partially_amended_statute_rejected_as_unmodified():
    findings = [
        {
            "url": "https://www.federalregister.gov/documents/2025/10/15/rule-repeal",
            "title": "Federal Register Final Rule",
            "summary": "Federal Section 402 subsection (b) was repealed in 2025.",
            "publication_date": "2025-10-15",
        }
    ]
    vr = verify_report_evidence(
        "- Federal Section 402 subsection (b) is currently in effect.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] != "SUPPORTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.AMENDED_OR_SUPERSEDED.value


def test_dr5_3_2_l4_distinguishes_party_limited_injunction_from_nationwide_vacatur():
    findings = [
        {
            "url": "https://www.uscourts.gov/opinions/2026/party-injunction.pdf",
            "title": "District Court Order on Preliminary Injunction",
            "summary": "The federal agency rule under 17 C.F.R. 240.10b-5 is subject to a preliminary injunction limited to named plaintiffs.",
            "publication_date": "2026-03-10",
        }
    ]
    vr = verify_report_evidence(
        "- 17 C.F.R. 240.10b-5 was vacated nationwide by the court.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "INJUNCTION_VS_VACATUR_SCOPE_MISMATCH"


def test_dr5_3_2_l5_undated_or_historical_statute_never_marked_current_verified():
    findings = [
        {
            "url": "https://uscode.house.gov/view.xhtml?req=granuleid:USC-1998-title15-section78j",
            "title": "15 U.S.C. 78j (1998 Archival Text)",
            "summary": "15 U.S.C. 78j prohibits manipulative devices in securities transactions (1998 text).",
        }
    ]
    vr = verify_report_evidence(
        "- 15 U.S.C. 78j prohibits manipulative devices in securities transactions (1998 text).",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "SUPPORTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value


def test_dr5_3_2_l6_circuit_split_requires_matching_posture_else_procedural_divergence():
    # Case A: 9th Cir. final merits vs. 5th Cir. preliminary injunction -> PROCEDURAL_OR_POSTURE_DIVERGENCE
    findings_mixed = [
        {
            "url": "https://www.uscourts.gov/ca9/2026/merits.pdf",
            "title": "9th Cir. Final Merits Opinion",
            "summary": "The 9th Cir. on final merits upheld federal Section 230 immunity.",
            "publication_date": "2026-04-01",
        },
        {
            "url": "https://www.uscourts.gov/ca5/2026/pi.pdf",
            "title": "5th Cir. Preliminary Injunction Order",
            "summary": "The 5th Cir. on preliminary injunction enjoined federal Section 230 immunity.",
            "publication_date": "2026-04-02",
        },
    ]
    vr_mixed = verify_report_evidence(
        "- Federal Section 230 immunity applies nationwide.",
        findings_mixed,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca_mixed = vr_mixed.to_dict()["claim_assessments"][0]
    assert ca_mixed["claim_assessment"] == "CONTESTED"
    assert ca_mixed["evidence_classification"] == "PROCEDURAL_OR_POSTURE_DIVERGENCE"

    # Case B: Matching final merits posture in both circuits -> ESTABLISHED_CIRCUIT_SPLIT
    findings_merits = [
        {
            "url": "https://www.uscourts.gov/ca9/2026/merits.pdf",
            "title": "9th Cir. Final Merits Opinion",
            "summary": "The 9th Cir. on final merits upheld federal Section 230 immunity.",
            "publication_date": "2026-04-01",
        },
        {
            "url": "https://www.uscourts.gov/ca5/2026/merits.pdf",
            "title": "5th Cir. Final Merits Opinion",
            "summary": "The 5th Cir. on final merits invalidated federal Section 230 immunity.",
            "publication_date": "2026-04-02",
        },
    ]
    vr_merits = verify_report_evidence(
        "- Federal Section 230 immunity applies nationwide.",
        findings_merits,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca_merits = vr_merits.to_dict()["claim_assessments"][0]
    assert ca_merits["claim_assessment"] == "CONTESTED"
    assert ca_merits["evidence_classification"] == "ESTABLISHED_CIRCUIT_SPLIT"


def test_dr5_3_2_l7_federal_preemption_over_state_statute_surfaces_conflict():
    findings = [
        {
            "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=310",
            "title": "California Section 310",
            "summary": "California Section 310 mandates state-specific labeling rules.",
            "publication_date": "2026-01-10",
        },
        {
            "url": "https://www.supremecourt.gov/opinions/26pdf/preemption.pdf",
            "title": "Supreme Court Preemption Ruling",
            "summary": "Federal law holds California Section 310 is preempted by federal law.",
            "publication_date": "2026-06-15",
        },
    ]
    vr = verify_report_evidence(
        "- California Section 310 mandates state-specific labeling rules.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "FEDERAL_PREEMPTION_CONFLICT"


def test_dr5_3_2_l8_conflicting_effective_dates_and_entity_staggered_dates():
    findings = [
        {
            "url": "https://www.ecfr.gov/current/title-17/section-229",
            "title": "17 C.F.R. 229 Compliance Schedule",
            "summary": "17 C.F.R. 229 applies to smaller reporting companies effective January 1, 2027.",
            "publication_date": "2026-02-01",
        }
    ]
    vr = verify_report_evidence(
        "- 17 C.F.R. 229 applies to large accelerated filers effective January 1, 2026.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value


def test_dr5_3_2_l9_unverified_legal_blog_and_pii_sanitization():
    findings = [
        {
            "url": "https://random-law-seo-blog.example.com/post",
            "title": "Law Marketing Post",
            "summary": "42 U.S.C. 1983 guarantees $5,000 automatic recovery (SSN 123-45-6789).",
        }
    ]
    vr = verify_report_evidence(
        "- 42 U.S.C. 1983 guarantees $5,000 automatic recovery.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert "123-45-6789" not in str(ca)


def test_dr5_3_2_l10_cold_start_isolation_when_disabled():
    code = (
        "import sys\n"
        "from src.research_verifier import verify_report_evidence\n"
        "vr = verify_report_evidence('- 15 U.S.C. 78j prohibits fraud.', [{'url': 'https://uscode.house.gov/78j', 'title': 'USC', 'summary': '15 U.S.C. 78j prohibits fraud.'}], enable_domain_verifiers=False, category='legal')\n"
        "assert 'src.verifiers.legal' not in sys.modules\n"
        "assert 'src.verifiers' not in sys.modules\n"
        "assert 'claim_assessments' not in vr.to_dict()\n"
        "print('DR5_3_2_L10_COLD_START_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_3_2_L10_COLD_START_OK" in proc.stdout


def test_dr5_3_2_l11_section_and_subsection_specific_effective_dates_isolation():
    findings = [
        {
            "url": "https://www.congress.gov/bill/119th-congress/section-104",
            "title": "Federal Section 104 Text",
            "summary": (
                "Federal Section 104 subsection (b) was amended effective January 1, 2026. "
                "Federal Section 104 subsection (c) remains currently in effect as enacted in 2024."
            ),
            "publication_date": "2026-01-05",
        }
    ]
    # Subsection (c) must NOT be falsely invalidated by amendment to subsection (b)
    vr_c = verify_report_evidence(
        "- Federal Section 104 subsection (c) remains currently in effect as enacted in 2024.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca_c = vr_c.to_dict()["claim_assessments"][0]
    assert ca_c["claim_assessment"] == "SUPPORTED"
    assert ca_c["legal_currency"] == LegalCurrencyStatus.CURRENT_VERIFIED.value


def test_dr5_3_2_l12_subsequent_appellate_reversal_and_en_banc_supersession():
    findings = [
        {
            "url": "https://www.uscourts.gov/ca9/2024/panel.pdf",
            "title": "2024 Panel Opinion on Federal Section 501",
            "summary": "In 2024, the 9th Cir. panel held Federal Section 501 permits warrantless inspection.",
            "publication_date": "2024-05-10",
        },
        {
            "url": "https://www.uscourts.gov/ca9/2026/enbanc.pdf",
            "title": "2026 En Banc Reversal on Federal Section 501",
            "summary": "In 2026, the 2024 panel ruling on Federal Section 501 was vacated on en banc review and reversed by the Supreme Court.",
            "publication_date": "2026-07-20",
        },
    ]
    vr = verify_report_evidence(
        "- Federal Section 501 permits warrantless inspection as controlling precedent.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.AMENDED_OR_SUPERSEDED.value
    assert ca["evidence_classification"] == "SUPERSEDED_OR_REVERSED_AUTHORITY"
