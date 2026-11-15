"""DR-5.1 Standalone Source Authority, Provenance Graph & Cold-Start Isolation Tests."""

from __future__ import annotations

import subprocess
import sys

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    ProvenanceRelation,
    PublicationStatus,
    PublicationType,
    SourceClass,
    assess_source_authority,
    build_passage_evidence_ref,
    build_provenance_graph,
)


def test_dr5_1_fail_closed_unverified_venue_on_missing_metadata():
    sa = assess_source_authority({
        "url": "https://random-tech-blog.example.org/gpu-rumor",
        "title": "New GPU Specs",
        "summary": "Some specs listed without primary attribution.",
    })
    assert sa.source_class == SourceClass.UNVERIFIED_VENUE
    assert sa.publication_status == PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED
    assert sa.has_independent_methodology is False


def test_dr5_1_doi_and_journal_editorial_never_auto_establish_peer_review():
    doi_only = assess_source_authority({
        "url": "https://doi.org/10.1038/s41586-025-00001-x",
        "title": "Nature Article Record",
        "summary": "DOI resolver landing page without peer-review verification.",
    })
    assert doi_only.source_class == SourceClass.INDEX_OR_RESOLVER
    assert doi_only.publication_status == PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED

    editorial = assess_source_authority({
        "url": "https://journal-example.org/article/123",
        "title": "Editorial: Reflections on Trial Outcomes",
        "summary": "Peer-reviewed journal Volume 12, Issue 4, Editorial commentary.",
    })
    assert editorial.publication_type == PublicationType.EDITORIAL_OR_LETTER
    assert editorial.publication_status == PublicationStatus.EDITORIAL_NON_RESEARCH


def test_dr5_1_legal_currency_defaults_to_current_status_not_established():
    statute = assess_source_authority({
        "url": "https://www.law.cornell.edu/uscode/text/26/162",
        "title": "26 U.S.C. ? 162 - Trade or business expenses",
        "summary": "There shall be allowed as a deduction all the ordinary and necessary expenses paid or incurred.",
    })
    assert statute.legal_currency == LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED


def test_dr5_1_provenance_graph_syndication_anti_transitivity_and_unknown_safeguard():
    # A: Official NVIDIA spec sheet (PRIMARY_INSTITUTIONAL)
    a = assess_source_authority({
        "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
        "title": "NVIDIA GeForce RTX 5090 Specifications",
        "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP.",
    })
    # B: Rewritten secondary summary derived from NVIDIA announcement (no independent test methodology)
    b = assess_source_authority({
        "url": "https://news-outlet-b.example.com/rtx-5090-launch",
        "title": "NVIDIA Announces RTX 5090",
        "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP.",
    })
    # C: Independent lab benchmark with explicit test rig methodology
    c = assess_source_authority({
        "url": "https://www.techpowerup.com/review/nvidia-geforce-rtx-5090/",
        "title": "NVIDIA RTX 5090 Review",
        "summary": "Tested on our GPU test bench using power analyzer and driver version 570.12: RTX 5090 uses 575W under load with 32 GB GDDR7.",
    })
    # D: Secondary summary with UNKNOWN independence (no methodology, low overlap with A)
    d = assess_source_authority({
        "url": "https://another-summary.example.net/gpu-roundup",
        "title": "2025 GPU Roundup",
        "summary": "Flagship cards now ship with 32 GB VRAM.",
    })

    edges, summary = build_provenance_graph([a, b, c, d])
    # B must collapse into A; C remains an independent measurement; D (UNKNOWN) must NEVER be counted as verified independent corroboration
    assert summary["collapsed_duplicate_count"] == 1
    assert summary["verified_independent_origin_count"] == 2
    assert set(summary["verified_independent_origins"]) == {a.url, c.url}
    assert d.url in summary["unverified_independence_urls"]
    assert any(e.relation == ProvenanceRelation.INDEPENDENT_MEASUREMENT and e.confidence == DependenceConfidence.INDEPENDENT for e in edges)


def test_dr5_1_passage_evidence_ref_sanitizes_pii_and_records_versions():
    sa = assess_source_authority({
        "url": "https://www.nvidia.com/en-us/geforce/rtx-5090/",
        "title": "RTX 5090 Spec",
        "summary": "Contact admin@example.com or 555-123-4567 with token sk-1234567890abcdefghij; RTX 5090 has 32 GB GDDR7.",
    })
    ref = build_passage_evidence_ref(sa, query_or_claim_term="32 GB GDDR7")
    assert "[REDACTED_EMAIL]" in ref.excerpt
    assert "[REDACTED_PHONE]" in ref.excerpt
    assert "[REDACTED_CREDENTIAL]" in ref.excerpt
    assert "admin@example.com" not in ref.excerpt
    assert "sk-1234567890abcdefghij" not in ref.excerpt
    assert ref.extraction_version == "extract-v1.0"
    assert ref.normalization_version == "norm-v1.0"
    assert len(ref.content_sha256) == 64


def test_dr5_1_genuine_subprocess_cold_start_isolation():
    """Fresh Python subprocess running DR-4.2 baseline must never import src.research_sources."""
    code = (
        "import sys; "
        "from src.deep_research import DeepResearcher; "
        "from src.research_verifier import verify_report_evidence; "
        "r = DeepResearcher(llm_endpoint='http://127.0.0.1:11434/v1', llm_model='gemma4:31b'); "
        "vr = verify_report_evidence('- RTX 5090 has 32 GB VRAM.', [{'url': 'https://www.nvidia.com/rtx-5090', 'title': 'NVIDIA', 'summary': 'RTX 5090 has 32 GB VRAM.'}]); "
        "assert 'src.research_sources' not in sys.modules, 'src.research_sources was unexpectedly imported!'; "
        "assert 'RTX 5090: 32 GB' in vr.verified_facts; "
        "print('COLD_START_OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "COLD_START_OK" in proc.stdout
