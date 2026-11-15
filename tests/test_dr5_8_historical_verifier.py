"""DR-5.8 tests for hardened historical domain verifier plugin."""
import subprocess
import sys

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
