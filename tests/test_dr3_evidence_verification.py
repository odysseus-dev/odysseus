"""DR-3 unit tests for claim, specification, conflict, and citation verification."""

from __future__ import annotations

from src.research_verifier import verify_report_evidence


def test_detects_cross_model_spec_conflation_and_unsupported_claims():
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
            "title": "NVIDIA GeForce RTX 4090 Official Specs",
            "summary": "The NVIDIA GeForce RTX 4090 features 24 GB GDDR6X memory and 450W TDP.",
        },
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
            "title": "NVIDIA GeForce RTX 5090 Official Specs",
            "summary": "The NVIDIA GeForce RTX 5090 features 32 GB GDDR7 memory and 575W TDP.",
        },
    ]

    # Report falsely attributes RTX 5090's 32 GB & 575W to RTX 4090, while keeping RTX 5090 accurate
    report = (
        "## GPU Comparison\n"
        "- The RTX 4090 comes with 32 GB VRAM and a 575W power draw.\n"
        "- The RTX 5090 comes with 32 GB VRAM and a 575W power draw.\n"
    )

    vr = verify_report_evidence(report, findings)
    assert "RTX 5090: 32 GB" in vr.verified_facts
    assert "RTX 5090: 575W" in vr.verified_facts
    # RTX 4090 with 32 GB / 575W must be flagged as unsupported!
    assert "RTX 4090: 32 GB" in vr.unsupported_claims
    assert "RTX 4090: 575W" in vr.unsupported_claims
    assert "RTX 4090 official specifications" in vr.followup_queries


def test_detects_conflicting_evidence_across_sources():
    findings = [
        {
            "url": "https://source-a.example.com/gpu",
            "title": "Source A",
            "summary": "Early leak claims RTX 5090 TDP is 500W.",
        },
        {
            "url": "https://source-b.example.com/gpu",
            "title": "Source B",
            "summary": "Official spec sheet confirms RTX 5090 TDP is 575W.",
        },
    ]
    vr = verify_report_evidence("RTX 5090 uses 575W.", findings)
    assert any("RTX 5090" in c and "conflicting W values" in c for c in vr.conflicting_evidence)


def test_flags_fabricated_citations_not_in_fetched_sources():
    findings = [
        {
            "url": "https://www.nvidia.com/rtx-5090",
            "title": "NVIDIA RTX 5090",
            "summary": "RTX 5090 has 32 GB GDDR7.",
        }
    ]
    report = (
        "According to [NVIDIA](https://www.nvidia.com/rtx-5090) and "
        "[Fake Benchmark](https://fabricated-bench.example.invalid/rtx5090), the RTX 5090 has 32 GB."
    )
    vr = verify_report_evidence(report, findings)
    assert len(vr.unverified_citations) == 1
    assert "fabricated-bench.example.invalid" in vr.unverified_citations[0]
    md = vr.to_markdown()
    assert "## Evidence Verification Summary" in md
    assert "[Verified]" in md
    assert "[Unverified Citation]" in md
