"""DR-5.3.3 Medical & Scientific Domain Verifier Adversarial & Isolation Tests (M1-M12)."""

from __future__ import annotations

import subprocess
import sys

from src.research_sources import LegalCurrencyStatus, PublicationStatus
from src.research_verifier import verify_report_evidence
from src.verifiers.base import build_default_registry


def test_dr5_3_3_m1_observational_study_cannot_substantiate_causal_cure():
    findings = [
        {
            "url": "https://pubmed.ncbi.nlm.nih.gov/39001122/",
            "title": "Retrospective Cohort Study of Drug X",
            "summary": "In an observational cohort study (n = 4,200), Drug X was associated with lower biomarker levels.",
            "publication_date": "2026-02-10",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- Drug X causally prevents and cures heart failure in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "CAUSAL_CLAIM_FROM_OBSERVATIONAL_STUDY"


def test_dr5_3_3_m2_systematic_review_does_not_auto_override_newer_phase3_rct():
    # Case A: Older 2019 systematic review conflicts with newer 2026 Phase 3 double-blind RCT -> CONTESTED
    findings = [
        {
            "url": "https://www.cochranelibrary.com/cdsr/doi/10.1002/14651858.CD012345/full",
            "title": "2019 Cochrane Systematic Review on Drug Alpha",
            "summary": "A 2019 systematic review (n = 900, high attrition) found Drug Alpha ineffective for all-cause mortality in adult patients.",
            "publication_date": "2019-06-01",
            "is_peer_reviewed": True,
        },
        {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMoa2600111",
            "title": "2026 Multicenter Phase 3 RCT of Drug Alpha",
            "summary": "A 2026 double-blind multicenter Phase 3 randomized controlled trial (n = 3,400) showed Drug Alpha reduces all-cause mortality in adult patients.",
            "publication_date": "2026-05-15",
            "is_peer_reviewed": True,
        },
    ]
    vr = verify_report_evidence(
        "- Drug Alpha reduces all-cause mortality in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "CLINICAL_TRIAL_VS_SYSTEMATIC_REVIEW_CONFLICT"

    # Case B: Small pilot study cannot be elevated to "established medical consensus"
    pilot_findings = [
        {
            "url": "https://pubmed.ncbi.nlm.nih.gov/39112233/",
            "title": "Single-Center Pilot Study of Drug Beta",
            "summary": "In a single-center pilot study (n = 18), Drug Beta improves clinical cure in adult patients.",
            "publication_date": "2026-03-01",
            "is_peer_reviewed": True,
        }
    ]
    vr_pilot = verify_report_evidence(
        "- Drug Beta is established medical consensus for clinical cure in adult patients.",
        pilot_findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_pilot = vr_pilot.to_dict()["claim_assessments"][0]
    assert ca_pilot["claim_assessment"] == "CONTESTED"
    assert ca_pilot["evidence_classification"] == "SINGLE_STUDY_NOT_SYSTEMATIC_CONSENSUS"


def test_dr5_3_3_m3_preprint_never_classified_as_peer_reviewed():
    findings = [
        {
            "url": "https://www.medrxiv.org/content/10.1101/2026.04.01.26300111v1",
            "title": "medRxiv Preprint: Drug Gamma Trial",
            "summary": "Preprint (10.1101/2026.04.01.26300111v1): Drug Gamma 20 mg oral reduces hospitalization in adult patients.",
            "publication_date": "2026-04-01",
        }
    ]
    vr = verify_report_evidence(
        "- Peer-reviewed clinical trial proof shows Drug Gamma 20 mg oral reduces hospitalization in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["publication_status"] == "PREPRINT_NOT_PEER_REVIEWED"
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "PREPRINT_PRESENTED_AS_PEER_REVIEWED"


def test_dr5_3_3_m4_three_way_retraction_concern_and_linked_erratum():
    # (a) Retracted article -> excluded completely
    retracted_findings = [
        {
            "url": "https://www.thelancet.com/journals/lancet/article/PIIS0140-6736(25)00999-1/fulltext",
            "title": "Retraction Notice: Drug Delta Trial",
            "summary": "Retracted: Drug Delta 50 mg oral reduces all-cause mortality in adult patients.",
            "publication_date": "2025-11-10",
            "is_peer_reviewed": True,
        }
    ]
    vr_ret = verify_report_evidence(
        "- Drug Delta 50 mg oral reduces all-cause mortality in adult patients.",
        retracted_findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_ret = vr_ret.to_dict()["claim_assessments"][0]
    assert ca_ret["publication_status"] == "RETRACTED"
    assert ca_ret["claim_assessment"] != "SUPPORTED"

    # (b) Expression of concern -> flagged as CONTESTED
    eoc_findings = [
        {
            "url": "https://jamanetwork.com/journals/jama/fullarticle/2812345",
            "title": "Editorial Expression of Concern: Drug Epsilon Trial",
            "summary": "Editorial expression of concern issued regarding Drug Epsilon 15 mg oral for stroke prevention in adult patients.",
            "publication_date": "2026-01-20",
            "is_peer_reviewed": True,
        }
    ]
    vr_eoc = verify_report_evidence(
        "- Drug Epsilon 15 mg oral reduces stroke in adult patients.",
        eoc_findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_eoc = vr_eoc.to_dict()["claim_assessments"][0]
    assert ca_eoc["publication_status"] == "EXPRESSION_OF_CONCERN"
    assert ca_eoc["claim_assessment"] == "CONTESTED"

    # (c) Corrigendum linked via DOI to original publication confirming post-correction dosage -> SUPPORTED
    linked_erratum_findings = [
        {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMoa2500777",
            "title": "Drug Zeta Phase 3 Trial",
            "summary": "Original study (10.1056/NEJMoa2500777): Drug Zeta oral reduces hospitalization in adult patients.",
            "publication_date": "2025-09-01",
            "is_peer_reviewed": True,
        },
        {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMx2600112",
            "title": "Published Correction for 10.1056/NEJMoa2500777",
            "summary": "Corrigendum to 10.1056/NEJMoa2500777: corrected value confirms Drug Zeta 25 mg oral reduces hospitalization in adult patients.",
            "publication_date": "2026-01-12",
            "is_peer_reviewed": True,
        },
    ]
    vr_cor = verify_report_evidence(
        "- Drug Zeta 25 mg oral reduces hospitalization in adult patients.",
        linked_erratum_findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_cor = vr_cor.to_dict()["claim_assessments"][0]
    assert ca_cor["claim_assessment"] == "SUPPORTED"
    assert ca_cor["publication_status"] == "PEER_REVIEWED_CORRECTED"


def test_dr5_3_3_m5_animal_and_in_vitro_research_isolated_from_human_claims():
    findings = [
        {
            "url": "https://www.nature.com/articles/s41591-026-09999-x",
            "title": "Preclinical Xenograft Study of Compound-9",
            "summary": "In murine xenograft models and in vitro cell lines, Compound-9 shrinks tumor volume.",
            "publication_date": "2026-03-14",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- Compound-9 is effective in human patients with solid tumors.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "PRECLINICAL_TRANSLATIONAL_MISMATCH"


def test_dr5_3_3_m6_independent_dosage_route_population_and_endpoint_isolation():
    base_finding = [
        {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMoa2600222",
            "title": "Drug Theta Phase 3 Trial",
            "summary": "Phase 3 randomized controlled trial (n = 2,100): Drug Theta 10 mg oral lowers LDL cholesterol in adult patients.",
            "publication_date": "2026-02-20",
            "is_peer_reviewed": True,
        }
    ]
    for claim_line, expected_cls in [
        ("- Drug Theta 100 mg oral lowers LDL cholesterol in adult patients.", "DOSAGE_MISMATCH"),
        ("- Drug Theta 10 mg intravenous lowers LDL cholesterol in adult patients.", "ROUTE_MISMATCH"),
        ("- Drug Theta 10 mg oral lowers LDL cholesterol in pediatric patients.", "POPULATION_MISMATCH"),
        ("- Drug Theta 10 mg oral reduces all-cause mortality in adult patients.", "SURROGATE_VS_CLINICAL_ENDPOINT_MISMATCH"),
    ]:
        vr = verify_report_evidence(
            claim_line,
            base_finding,
            enable_domain_verifiers=True,
            category="medical",
            registry=build_default_registry(),
        )
        ca = vr.to_dict()["claim_assessments"][0]
        assert ca["claim_assessment"] == "CONTESTED"
        assert ca["evidence_classification"] == expected_cls


def test_dr5_3_3_m7_conflicting_clinical_guidelines_preserved_without_recency_winner():
    findings = [
        {
            "url": "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/screening-a",
            "title": "USPSTF Guideline",
            "summary": "USPSTF guideline recommends routine screening in adult patients.",
            "publication_date": "2022-05-01",
        },
        {
            "url": "https://www.heart.org/en/guidelines/screening-a",
            "title": "AHA Guideline",
            "summary": "AHA guideline states routine screening is not recommended in adult patients.",
            "publication_date": "2026-06-01",
        },
    ]
    vr = verify_report_evidence(
        "- USPSTF guideline recommends routine screening in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "CONFLICTING_CLINICAL_GUIDELINES"


def test_dr5_3_3_m8_unverified_supplement_blog_and_fabricated_citation_fail_closed():
    findings = [
        {
            "url": "https://miracle-supplement-wellness.example.com/cure",
            "title": "Wellness Blog",
            "summary": "Herbal Extract Z 500 mg oral cures diabetes in adult patients.",
        }
    ]
    vr = verify_report_evidence(
        "- Herbal Extract Z 500 mg oral cures diabetes in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"


def test_dr5_3_3_m9_defense_in_depth_phi_sanitization_mrn_dob_names_ocr():
    findings = [
        {
            "url": "https://clinicaltrials.gov/study/NCT04512345",
            "title": "Trial NCT04512345 Results",
            "summary": (
                "NCT04512345 Phase 3 randomized controlled trial (n = 1,420): Drug Omega 500 mg oral "
                "reduces hospitalization in adult patients (HR 0.74, p < 0.001). "
                "Patient John Doe, MRN: 987-65-432, DOB: 1984-07-19, 742 Evergreen Terrace, "
                "555-867-5309, john.doe@example.org, M R N : 1 2 3 - 4 5 - 6 7 8."
            ),
            "publication_date": "2026-05-01",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- NCT04512345 shows Drug Omega 500 mg oral reduces hospitalization in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    serialized = str(ca)
    assert ca["claim_assessment"] == "SUPPORTED"
    for leaked in ("John Doe", "987-65-432", "1984-07-19", "742 Evergreen Terrace", "555-867-5309", "john.doe@example.org", "1 2 3 - 4 5 - 6 7 8"):
        assert leaked not in serialized
    assert "500 mg" in serialized
    assert "NCT04512345" in serialized
    assert "not HIPAA compliance certification" in ca["authority_scope_note"]


def test_dr5_3_3_m10_cold_start_isolation_when_disabled():
    code = (
        "import sys\n"
        "from src.research_verifier import verify_report_evidence\n"
        "vr = verify_report_evidence('- Drug Omega 500 mg oral reduces hospitalization.', [{'url': 'https://clinicaltrials.gov/study/NCT04512345', 'title': 'Trial', 'summary': 'Drug Omega 500 mg oral reduces hospitalization.'}], enable_domain_verifiers=False, category='medical')\n"
        "assert 'src.verifiers.medical' not in sys.modules\n"
        "assert 'src.verifiers' not in sys.modules\n"
        "assert 'claim_assessments' not in vr.to_dict()\n"
        "print('DR5_3_3_M10_COLD_START_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_3_3_M10_COLD_START_OK" in proc.stdout


def test_dr5_3_3_m11_six_axis_clinical_tuple_and_separate_temporal_axis():
    findings = [
        {
            "url": "https://www.nejm.org/doi/full/10.1056/NEJMoa2600888",
            "title": "Drug Sigma 2026 Phase 3 RCT",
            "summary": "In 2026, a double-blind multicenter Phase 3 randomized controlled trial (n = 1,850) showed Drug Sigma 40 mg oral reduces stroke in adult patients.",
            "publication_date": "2026-04-10",
            "is_peer_reviewed": True,
        }
    ]
    # Matching 6-axis scope + matching 2026 temporal axis -> SUPPORTED + CURRENT_VERIFIED
    vr_ok = verify_report_evidence(
        "- In 2026, Drug Sigma 40 mg oral reduces stroke in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_ok = vr_ok.to_dict()["claim_assessments"][0]
    assert ca_ok["claim_assessment"] == "SUPPORTED"
    assert ca_ok["legal_currency"] == LegalCurrencyStatus.CURRENT_VERIFIED.value

    # Mismatched temporal year (2021 vs 2026) -> CONTESTED (TEMPORAL_VERSION_MISMATCH)
    vr_old = verify_report_evidence(
        "- In 2021, Drug Sigma 40 mg oral reduces stroke in adult patients.",
        findings,
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca_old = vr_old.to_dict()["claim_assessments"][0]
    assert ca_old["claim_assessment"] == "CONTESTED"
    assert ca_old["evidence_classification"] == "TEMPORAL_VERSION_MISMATCH"


def test_dr5_3_3_m12_missing_evidence_never_becomes_clinical_advice():
    vr = verify_report_evidence(
        "- Compound-99 10 mg oral cures pancreatic cancer in adult patients.",
        [],
        enable_domain_verifiers=True,
        category="medical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert "not clinical diagnosis or medical advice" in ca["authority_scope_note"]
