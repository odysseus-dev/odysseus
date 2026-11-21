"""DR-5.8 tests for hardened historical domain verifier plugin (unified with DR-5.3.4 rules)."""
import json
import subprocess
import sys

from src.research_sources import LegalCurrencyStatus
from src.research_verifier import verify_report_evidence
from src.verifiers.base import build_default_registry


def test_H1_primary_archival_evidence_with_matching_bce_era_corroborates_claim():
    findings = [
        {
            "url": "https://www.loc.gov/exhibits/roman-republic/44bce.html",
            "title": "Library of Congress Archival Record: Roman Senate 44 BCE",
            "summary": "Primary source archival record: In 44 BCE, the Roman Senate adopted the decree in Rome.",
            "publication_date": "2024-05-01",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- In 44 BCE, the Roman Senate adopted the decree in Rome.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    assert vr.claim_assessments is not None
    hist = [a for a in vr.claim_assessments if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "SUPPORTED"
    assert hist[0]["plugin_name"] == "historical_verifier"


def test_H1b_cross_era_bce_vs_ce_conflation_rejected_as_contested():
    findings = [
        {
            "url": "https://archives.gov/exhibits/chronicle-44ce.html",
            "title": "Archival Chronicle 44 CE",
            "summary": "Primary source chronicle: In 44 CE, the Roman Senate enacted the provincial edict.",
            "publication_date": "2024-03-10",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- In 44 BCE, the Roman Senate enacted the provincial edict.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert hist[0]["evidence_classification"] == "ERA_OR_CHRONOLOGY_MISMATCH"
    assert vr.has_gaps() is True


def test_H1c_approximate_circa_source_prevents_unqualified_exact_date_support():
    findings = [
        {
            "url": "https://www.britishmuseum.org/collection/inscription-1200bce",
            "title": "British Museum Inscription Record",
            "summary": "Archival inscription dated circa 1200 BCE records the Bronze Age treaty.",
            "publication_date": "2025-01-15",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- In 1184 BCE, the Bronze Age treaty was signed.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"


def test_H2_unverified_tertiary_blog_never_auto_corroborates_historical_claim():
    findings = [
        {
            "url": "https://random-history-trivia-blog.example.com/1648",
            "title": "History Trivia Blog Post",
            "summary": "In 1648 CE, the Treaty of Westphalia ended the Thirty Years War.",
        }
    ]
    vr = verify_report_evidence(
        "- In 1648 CE, the Treaty of Westphalia ended the Thirty Years War.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert hist[0]["evidence_classification"] == "UNVERIFIED_TERTIARY_SOURCE"


def test_H3_historiographical_debate_preserved_as_contested_without_recency_winner():
    findings = [
        {
            "url": "https://www.cambridge.org/core/journals/historical-journal/article/battle-1066",
            "title": "Cambridge Historical Journal Analysis",
            "summary": "Peer-reviewed study: Historians debate the exact troop strength at the Battle of Hastings in 1066 CE.",
            "publication_date": "2026-06-01",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- In 1066 CE, the Battle of Hastings involved exactly 15,000 Norman troops.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert hist[0]["evidence_classification"] == "HISTORIOGRAPHICAL_DEBATE"


def test_H4_myth_and_legend_framing_fails_closed_on_literal_historical_claim():
    findings = [
        {
            "url": "https://www.oxfordacademic.oup.com/classical-antiquity/article/trojan-horse",
            "title": "Oxford Classical Antiquity Review",
            "summary": "Peer-reviewed analysis: The wooden Trojan Horse in 1184 BCE is attested in epic myth and apocryphal legend rather than contemporary archival records.",
            "publication_date": "2025-09-12",
            "is_peer_reviewed": True,
        }
    ]
    vr = verify_report_evidence(
        "- In 1184 BCE, Greek soldiers infiltrated Troy inside a wooden Trojan Horse.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert hist[0]["evidence_classification"] == "MYTH_OR_APOCRYPHAL_FRAMING"


def test_H5_cold_start_isolation_when_disabled():
    code = (
        "import sys; "
        "from src.research_verifier import verify_report_evidence; "
        "vr = verify_report_evidence('In 1648 CE, the Treaty of Westphalia was signed.', [{'url': 'https://loc.gov/w', 'text': 'In 1648 CE, the Treaty of Westphalia was signed.'}], enable_domain_verifiers=False); "
        "assert vr.claim_assessments is None; "
        "assert 'src.verifiers.historical' not in sys.modules"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_H6_julian_vs_gregorian_calendar_mismatch_rejected_as_contested():
    findings = [
        {
            "url": "https://www.loc.gov/item/october-revolution-julian-1917/",
            "title": "Library of Congress Archival Note",
            "summary": "The Petrograd uprising occurred in October 1917 Julian calendar.",
            "publication_date": "2023-11-01",
        }
    ]
    vr = verify_report_evidence(
        "- The Petrograd uprising occurred in October 1917 Gregorian calendar.",
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=build_default_registry(),
    )
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert hist[0]["evidence_classification"] == "CALENDAR_SYSTEM_MISMATCH"


def test_H7_anachronistic_polity_founding_attribution_rejected_as_contested():
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
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert hist[0]["evidence_classification"] == "ANACHRONISTIC_ENTITY_ATTRIBUTION"


def test_H8_forged_or_retracted_chronicle_downgraded_as_discredited():
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
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert hist[0]["evidence_classification"] == "DISCREDITED_OR_FORGED_SOURCE"


def test_H9_conflicting_casualty_estimates_preserved_without_consensus():
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
    hist = [a for a in d["claim_assessments"] if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "CONTESTED"
    assert len(hist[0]["conflicting_passages"]) >= 1
    assert len(d["conflict_records"]) == 1
    assert d["conflict_records"][0]["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS"


def test_H10_undated_historical_claim_fails_closed():
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
    hist = [a for a in (vr.claim_assessments or []) if a.get("domain") == "historical"]
    assert len(hist) == 1
    assert hist[0]["claim_assessment"] == "INSUFFICIENT_EVIDENCE"
    assert hist[0]["evidence_classification"] == "UNDATED_HISTORICAL_CLAIM"
    assert hist[0]["legal_currency"] == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value


def test_H11_sanitizes_modern_pii_while_preserving_historical_dates_and_catalog_ids():
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
