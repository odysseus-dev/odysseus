"""DR-5.1.1 Targeted Security Sanitization, Offset Reproducibility & Graph Edge-Case Tests."""

from __future__ import annotations

import hashlib

from src.research_sources import (
    DependenceConfidence,
    ProvenanceRelation,
    assess_source_authority,
    build_passage_evidence_ref,
    build_provenance_graph,
    reconstruct_sanitized_text,
)


def test_dr5_1_1_redacts_synthetic_jwt_pem_routing_and_account_without_stripping_plain_numbers():
    # Synthetic credentials only ? never real secrets
    synthetic_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    synthetic_pem = "-----BEGIN PRIVATE KEY----- MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCsynthetic -----END PRIVATE KEY-----"
    sa = assess_source_authority({
        "url": "https://sec.gov/Archives/edgar/data/0000000000/synthetic-10k.htm",
        "title": "Synthetic Filing",
        "summary": (
            f"Token {synthetic_jwt} and key {synthetic_pem} with ABA routing # 021000021 "
            "and bank account number: 987654321012. Meanwhile 123456789 CUDA cycles and "
            "240000000 transistors remain ordinary unlabelled numbers alongside 32 GB VRAM."
        ),
    })

    # Safeguard A: Even intermediate SourceAssessment.normalized_text must already be sanitized
    assert synthetic_jwt not in sa.normalized_text
    assert "BEGIN PRIVATE KEY" not in sa.normalized_text
    assert "021000021" not in sa.normalized_text
    assert "987654321012" not in sa.normalized_text
    assert "[REDACTED_JWT]" in sa.normalized_text
    assert "[REDACTED_PRIVATE_KEY]" in sa.normalized_text
    assert "[REDACTED_ROUTING]" in sa.normalized_text
    assert "[REDACTED_ACCOUNT]" in sa.normalized_text
    # Ordinary unlabelled numeric identifiers must NOT be indiscriminately redacted
    assert "123456789" in sa.normalized_text
    assert "240000000" in sa.normalized_text


def test_dr5_1_1_redacts_ocr_whitespace_corrupted_credentials():
    ocr_corrupted = "Bearer eyJ hbGciOiJIUzI1NiJ9.eyJzdWIiOiJvY3IifQ.c2lnbmF0dXJlMTIzNDU2Nzg5MA and sk- 1234567890abcdefghij"
    sa = assess_source_authority({
        "url": "https://example.org/ocr-scan",
        "title": "OCR Scan",
        "summary": f"Header {ocr_corrupted} found before RTX 5090 575W TDP measurement.",
    })
    assert "eyJ hbGci" not in sa.normalized_text
    assert "1234567890abcdefghij" not in sa.normalized_text
    assert "[REDACTED_CREDENTIAL]" in sa.normalized_text


def test_dr5_1_1_passage_offsets_index_exact_sanitized_versioned_representation():
    sa = assess_source_authority({
        "url": "https://www.nvidia.com/en-us/geforce/rtx-5090/",
        "title": "Official Spec",
        "summary": (
            "Contact analyst@example.com or 555-987-6543 with routing # 111000025 and "
            "account # 123456789012; official RTX 5090 memory is 32 GB GDDR7."
        ),
    })
    ref = build_passage_evidence_ref(sa, query_or_claim_term="32 GB GDDR7")
    reconstructed = reconstruct_sanitized_text(sa)

    assert reconstructed[ref.char_start : ref.char_end] == ref.excerpt
    assert ref.sanitized_text_sha256 == hashlib.sha256(reconstructed.encode("utf-8")).hexdigest()
    assert ref.content_sha256 != ref.sanitized_text_sha256
    assert ref.extraction_version == "extract-v1.0"
    assert ref.normalization_version == "norm-v1.0"


def test_dr5_1_1_provenance_graph_cycles_mixed_edges_and_multi_lab_counting():
    # S1: Official vendor spec
    s1 = assess_source_authority({
        "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
        "title": "NVIDIA RTX 5090 Specs",
        "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP per Form 8-K announcement.",
    })
    # S2 & S3: Two syndicated/derived summaries repeating S1 announcement (mutual cycle / shared filing)
    s2 = assess_source_authority({
        "url": "https://outlet-two.example.com/rtx-5090",
        "title": "RTX 5090 Launch",
        "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP per Form 8-K announcement.",
    })
    s3 = assess_source_authority({
        "url": "https://outlet-three.example.com/rtx-5090",
        "title": "RTX 5090 News",
        "summary": "NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP per Form 8-K announcement.",
    })
    # S4: Independent Lab 1 with explicit test bench methodology
    s4 = assess_source_authority({
        "url": "https://www.techpowerup.com/review/nvidia-geforce-rtx-5090/",
        "title": "TechPowerUp RTX 5090 Review",
        "summary": "Tested on our GPU test bench using power analyzer and driver version 570.12: 575W load power.",
    })
    # S5: Independent Lab 2 with explicit frame-time & ambient temperature methodology
    s5 = assess_source_authority({
        "url": "https://gamersnexus.net/gpus/nvidia-rtx-5090-benchmarks",
        "title": "GamersNexus RTX 5090 Testing",
        "summary": "Measured in controlled ambient temperature chamber with frame time capture: 575W sustained.",
    })
    # S6: Blog citing shared study DOI without methodology (UNKNOWN independence)
    s6 = assess_source_authority({
        "url": "https://blog-six.example.net/gpu-notes",
        "title": "GPU Notes",
        "summary": "Discussion referencing DOI 10.1109/MICRO.2025.00042 on modern shader architecture.",
    })

    # Pass duplicate s1 entry as well to verify cycle/duplicate idempotence
    edges, summary = build_provenance_graph([s1, s2, s3, s4, s5, s6, s1])

    assert summary["total_sources"] == 6
    assert summary["collapsed_duplicate_count"] == 2
    assert summary["verified_independent_origin_count"] == 3
    assert set(summary["verified_independent_origins"]) == {s1.url, s4.url, s5.url}
    assert summary["unverified_independence_count"] == 1
    assert s6.url in summary["unverified_independence_urls"]
    # Pairwise independent measurement edges assessed per evidence relationship (S1-S4, S1-S5, S4-S5 = 3 pairs)
    assert summary["independent_measurement_pair_count"] == 3
    assert any(e.relation == ProvenanceRelation.INDEPENDENT_MEASUREMENT and e.confidence == DependenceConfidence.INDEPENDENT for e in edges)
