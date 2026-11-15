"""DR-5.3.4 Historical Domain Verifier Adversarial & Isolation Tests (10 tests)."""

from __future__ import annotations

import json
import subprocess
import sys

from src.research_sources import LegalCurrencyStatus
from src.research_verifier import verify_report_evidence
from src.verifiers.base import build_default_registry


def test_dr5_3_4_h1_primary_archival_and_peer_reviewed_corroboration():
    findings = [
        {
            "url": "https://www.archives.gov/milestone-documents/treaty-of-paris-1783",
            "title": "Treaty of Paris (1783) - National Archives",
            "summary": "The Treaty of Paris was signed in 1783 CE, ending the American Revolutionary War.",
            "publication_date": "2024-05-10",
        },
        {
            "url": "https://www.loc.gov/item/diplomatic-correspondence-treaty-of-paris-1783/",
            "title": "Library of Congress Diplomatic Records: Treaty of Paris 1783",
            "summary": "Archival diplomatic correspondence confirms the Treaty of Paris was signed in 1783 CE.",
            "publication_date": "2023-09-15",
            "has_independent_methodology": True,
        },
    ]
    vr = verify_report_evidence(
        "- The Treaty of Paris was signed in 1783 CE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    d = vr.to_dict()
    assert len(d["claim_assessments"]) == 1
    ca = d["claim_assessments"][0]
    assert ca["plugin_name"] == "historical_verifier"
    assert ca["claim_assessment"] == "SUPPORTED"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_VERIFIED.value
    assert ca["evidence_classification"] == "PRIMARY_CORROBORATED"


def test_dr5_3_4_h2_bce_ce_and_julian_gregorian_calendar_isolation():
    findings_era = [
        {
            "url": "https://www.britishmuseum.org/collection/rosetta-stone-196-bce",
            "title": "British Museum Archival Record: Rosetta Stone Decree",
            "summary": "The Memphis Decree on the Rosetta Stone was issued in 196 BCE.",
            "publication_date": "2024-01-20",
        }
    ]
    vr_era = verify_report_evidence(
        "- The Memphis Decree on the Rosetta Stone was issued in 196 CE.",
        findings_era,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca_era = vr_era.to_dict()["claim_assessments"][0]
    assert ca_era["claim_assessment"] == "CONTESTED"
    assert "CHRONOLOGICAL_DATE_OR_ERA_CONFLICT" in ca_era["rationale"]["directness"]

    findings_cal = [
        {
            "url": "https://www.loc.gov/item/october-revolution-julian-1917/",
            "title": "Library of Congress Archival Note",
            "summary": "The Petrograd uprising occurred in October 1917 Julian calendar.",
            "publication_date": "2023-11-01",
        }
    ]
    vr_cal = verify_report_evidence(
        "- The Petrograd uprising occurred in October 1917 Gregorian calendar.",
        findings_cal,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca_cal = vr_cal.to_dict()["claim_assessments"][0]
    assert ca_cal["claim_assessment"] == "CONTESTED"
    assert "CALENDAR_SYSTEM_MISMATCH" in ca_cal["rationale"]["directness"]


def test_dr5_3_4_h3_circa_approximate_range_never_verified_as_exact_date():
    findings = [
        {
            "url": "https://www.nature.com/articles/s41586-024-radiocarbon-vinland-settlement",
            "title": "Oxford Archaeological Radiocarbon Study",
            "summary": "The Vinland Norse settlement was established c. 1000 CE according to radiocarbon dating.",
            "publication_date": "2024-03-12",
            "is_peer_reviewed": True,
            "has_independent_methodology": True,
        }
    ]
    vr = verify_report_evidence(
        "- The Vinland Norse settlement was established in 1000 CE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "APPROXIMATE_RANGE_QUALIFIED"


def test_dr5_3_4_h4_anachronistic_polity_attribution_flagged():
    findings = [
        {
            "url": "https://www.bundesarchiv.de/en/historical-timeline-1871/",
            "title": "German Federal Archives Historical Timeline",
            "summary": "The unified German Empire was founded in 1871 CE.",
            "publication_date": "2024-02-01",
        }
    ]
    vr = verify_report_evidence(
        "- The unified German Empire was ratified in 1648 CE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "ANACHRONISTIC_ENTITY_ATTRIBUTION"


def test_dr5_3_4_h5_conflicting_casualty_and_attribution_estimates_preserve_conflict():
    findings = [
        {
            "url": "https://www.iwm.org.uk/history/somme-1916-dispatch",
            "title": "Imperial War Museums Archival Dispatch",
            "summary": "The Battle of the Somme in 1916 CE resulted in 420,000 casualties.",
            "publication_date": "2023-07-01",
        },
        {
            "url": "https://www.cambridge.org/core/journals/historical-journal/somme-1916",
            "title": "Cambridge Historical Journal Study",
            "summary": "The Battle of the Somme in 1916 CE resulted in 620,000 casualties.",
            "publication_date": "2024-06-15",
            "has_independent_methodology": True,
        },
    ]
    vr = verify_report_evidence(
        "- The Battle of the Somme in 1916 CE caused 420,000 casualties.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    d = vr.to_dict()
    ca = d["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert len(ca["conflicting_passages"]) >= 1
    assert len(d["conflict_records"]) == 1
    assert d["conflict_records"][0]["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS"


def test_dr5_3_4_h6_forged_or_retracted_chronicle_rejected():
    findings = [
        {
            "url": "https://www.vaticanarchive.example.org/donation-of-constantine-note",
            "title": "Archival Diplomatic Note on Donation of Constantine",
            "summary": "The Donation of Constantine was dated to 315 CE but is a proven medieval forgery and debunked.",
            "publication_date": "2024-04-01",
        }
    ]
    vr = verify_report_evidence(
        "- The Donation of Constantine was signed in 315 CE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "CONTESTED"
    assert ca["evidence_classification"] == "DISCREDITED_OR_FORGED_SOURCE"


def test_dr5_3_4_h7_syndicated_and_unverified_pop_history_blogs_fail_closed():
    findings = [
        {
            "url": "https://pop-history-listicles.blogspot.com/secret-library-fire",
            "title": "Top 10 Ancient Mysteries Blog",
            "summary": "The Library of Alexandria was completely destroyed in 48 BCE.",
        }
    ]
    vr = verify_report_evidence(
        "- The Library of Alexandria was destroyed in 48 BCE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert ca["evidence_classification"] == "UNVERIFIED_OR_UNSUPPORTED"


def test_dr5_3_4_h8_undated_historical_claim_fails_closed_on_chronology():
    findings = [
        {
            "url": "https://www.archives.gov/milestone-documents/magna-carta",
            "title": "Magna Carta - National Archives",
            "summary": "Magna Carta was sealed at Runnymede in 1215 CE.",
            "publication_date": "2024-01-10",
        }
    ]
    vr = verify_report_evidence(
        "- Magna Carta was sealed at Runnymede by King John.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    ca = vr.to_dict()["claim_assessments"][0]
    assert ca["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert ca["evidence_classification"] == "UNDATED_HISTORICAL_CLAIM"
    assert ca["legal_currency"] == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value


def test_dr5_3_4_h9_sanitizes_modern_pii_while_preserving_historical_dates_and_identifiers():
    findings = [
        {
            "url": "https://www.archives.gov/treaties/treaty-of-utrecht-1713",
            "title": "Treaty of Utrecht Archival Catalog MS-1713-B",
            "summary": (
                "The Treaty of Utrecht was signed in 1713 CE (Catalog MS-1713-B). "
                "Archivist contact: curator.jane.doe@archives.example.org token=sk-live-secret999888777."
            ),
            "publication_date": "2024-08-19",
        }
    ]
    vr = verify_report_evidence(
        "- The Treaty of Utrecht was signed in 1713 CE.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    serialized = json.dumps(vr.to_dict())
    assert "curator.jane.doe@archives.example.org" not in serialized
    assert "sk-live-secret999888777" not in serialized
    assert "1713" in serialized


def test_dr5_3_4_h10_cold_start_isolation_when_disabled():
    code = (
        "import sys; "
        "import src.deep_research; "
        "from src.research_verifier import verify_report_evidence; "
        "res = verify_report_evidence('- Treaty of Paris signed in 1783.', [{'url': 'https://archives.gov/1783', 'summary': 'Treaty of Paris signed in 1783.'}], enable_domain_verifiers=False, category='historical'); "
        "assert 'src.verifiers.historical' not in sys.modules; "
        "assert 'claim_assessments' not in res.to_dict(); "
        "print('OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "OK"
