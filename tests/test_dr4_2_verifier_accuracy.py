"""DR-4.2 Specification-to-Entity Proximity, Conflict Suppression & Corroboration Tests."""

from __future__ import annotations

from src.research_verifier import verify_report_evidence


def test_dr4_2_multi_entity_prose_never_cross_contaminates_specs():
    """RTX 4090 and RTX 5090 mentioned on the same line must keep their own specs strictly separate."""
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
            "title": "NVIDIA RTX 4090 Official Specs",
            "summary": "The RTX 4090 includes 24 GB GDDR6X memory and 450W TDP, while the RTX 5090 upgrades to 32 GB GDDR7 and 575W TDP.",
        },
        {
            "url": "https://www.techpowerup.com/gpu-specs/geforce-rtx-4090.c3889",
            "title": "TechPowerUp RTX 4090 & RTX 5090 Database",
            "summary": "NVIDIA RTX 4090 features 24 GB VRAM and 450W TDP; NVIDIA RTX 5090 features 32 GB VRAM and 575W TDP.",
        },
    ]
    report = (
        "# GPU Comparison\n"
        "- The RTX 4090 has 24 GB VRAM and 450W TDP, whereas the RTX 5090 has 32 GB VRAM and 575W TDP.\n"
    )
    vr = verify_report_evidence(report, findings)

    assert vr.conflicting_evidence == []
    assert "RTX 4090: 24 GB" in vr.verified_facts
    assert "RTX 4090: 450W" in vr.verified_facts
    assert "RTX 5090: 32 GB" in vr.verified_facts
    assert "RTX 5090: 575W" in vr.verified_facts

    all_verified = " | ".join(vr.verified_facts)
    assert "RTX 4090: 32 GB" not in all_verified
    assert "RTX 4090: 575W" not in all_verified
    assert "RTX 5090: 24 GB" not in all_verified
    assert "RTX 5090: 450W" not in all_verified


def test_dr4_2_markdown_comparison_table_binds_cells_to_column_headers():
    """Markdown table columns for RTX 4090 and RTX 5090 must bind specs exclusively to their column entity."""
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
            "title": "NVIDIA Specs",
            "summary": "RTX 4090 has 24 GB VRAM and 450W TDP. RTX 5090 has 32 GB VRAM and 575W TDP.",
        },
        {
            "url": "https://www.notebookcheck.net/RTX-4090-vs-RTX-5090.html",
            "title": "Notebookcheck GPU Comparison",
            "summary": "RTX 4090: 24 GB, 450W. RTX 5090: 32 GB, 575W.",
        },
    ]
    report = (
        "| Spec | RTX 4090 | RTX 5090 |\n"
        "| --- | --- | --- |\n"
        "| VRAM | 24 GB | 32 GB |\n"
        "| TDP | 450W | 575W |\n"
    )
    vr = verify_report_evidence(report, findings)
    assert vr.conflicting_evidence == []
    assert set(vr.verified_facts) == {"RTX 4090: 24 GB", "RTX 5090: 32 GB", "RTX 4090: 450W", "RTX 5090: 575W"}


def test_dr4_2_conflicting_values_never_receive_unqualified_verified_status():
    """When two sources disagree on RTX 4090 TDP (450W vs 575W), neither may appear in verified_facts."""
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/",
            "title": "Official NVIDIA Page",
            "summary": "RTX 4090 TDP is 450W.",
        },
        {
            "url": "https://rumor-blog.example.com/rtx-4090",
            "title": "Rumor Post",
            "summary": "RTX 4090 TDP is 575W.",
        },
    ]
    report = "- RTX 4090 operates at 450W TDP, though one source claimed 575W.\n"
    vr = verify_report_evidence(report, findings)

    assert any("RTX 4090 has conflicting W values" in c for c in vr.conflicting_evidence)
    assert "RTX 4090: 450W" not in vr.verified_facts
    assert "RTX 4090: 575W" not in vr.verified_facts


def test_dr4_2_distinguishes_corroborated_vs_single_source_vs_unsupported_prices_and_specs():
    """2+ sources -> verified_facts; 1 source -> single_source_claims; 0 sources -> unsupported_claims."""
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5090/",
            "title": "NVIDIA RTX 5090",
            "summary": "RTX 5090 features 32 GB GDDR7 and $1,999 MSRP.",
        },
        {
            "url": "https://www.anandtech.com/show/rtx-5090-review",
            "title": "RTX 5090 Review",
            "summary": "RTX 5090 comes with 32 GB memory.",
        },
    ]
    report = (
        "- RTX 5090 includes 32 GB VRAM at $1,999 MSRP, though some scalpers list RTX 5090 at $3,200 with 8 GB.\n"
    )
    vr = verify_report_evidence(report, findings)

    # 32 GB corroborated by 2 independent sources -> corroborated_facts
    assert "RTX 5090: 32 GB" in vr.corroborated_facts
    # $1,999 supported by 1 source only -> single_source_claims (NOT corroborated_facts)
    assert "RTX 5090: $1,999" in vr.single_source_claims
    assert "RTX 5090: $1,999" not in vr.corroborated_facts
    # $3,200 and 8 GB supported by 0 sources -> unsupported_claims
    assert "RTX 5090: $3,200" in vr.unsupported_claims
    assert "RTX 5090: 8 GB" in vr.unsupported_claims
