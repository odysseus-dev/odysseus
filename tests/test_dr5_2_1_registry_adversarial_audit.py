"""DR-5.2.1 Registry Isolation & Adversarial Architecture Audit Tests (A1 through A9)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PublicationStatus,
    assess_source_authority,
    build_passage_evidence_ref,
)
from src.research_verifier import verify_report_evidence
from src.verifiers.base import (
    ClaimAssessment,
    VerifierRegistry,
    build_default_registry,
    run_registry_verification,
)


class _StaticPlugin:
    def __init__(self, name: str, domain: str, categories: set[str], claims_fn):
        self.name = name
        self.domain = domain
        self._categories = categories
        self._claims_fn = claims_fn

    def supports_category(self, category: str) -> bool:
        return category in self._categories

    def verify(self, report_text, assessments, edges, provenance_summary, baseline_report):
        return self._claims_fn(report_text, assessments, edges, provenance_summary, baseline_report)


def _sample_rationale() -> dict[str, str]:
    return {
        "source_authority": "PRIMARY_INSTITUTIONAL",
        "independence": "Evaluated per evidence relationship",
        "directness": "DIRECT",
        "contradictions": "Preserved",
        "evidence_completeness": "Complete",
    }


def test_dr5_2_1_a1_duplicate_plugin_name_rejected_and_attribution_preserved():
    reg = VerifierRegistry()
    p1 = _StaticPlugin("alpha_verifier", "legal", {"legal"}, lambda *a: [])
    p2 = _StaticPlugin("alpha_verifier", "legal", {"legal"}, lambda *a: [])
    reg.register(p1)
    with pytest.raises(ValueError, match="Duplicate verifier plugin registration rejected"):
        reg.register(p2)


def test_dr5_2_1_a2_missing_domain_plugin_fails_closed_without_false_verification():
    findings = [
        {
            "url": "https://example.org/statute-draft",
            "title": "Unverified Statute Summary",
            "summary": "26 U.S.C. ? 162 allows deduction of ordinary expenses.",
        }
    ]
    # Empty registry or specialized category with no matching plugin must fail closed
    vr = verify_report_evidence(
        "- 26 U.S.C. ? 162 allows deduction.",
        findings,
        enable_domain_verifiers=True,
        category="legal",
        registry=VerifierRegistry(),
    )
    d = vr.to_dict()
    assert d["missing_domain_plugin"] is True
    assert d["claim_assessments"] == []


def test_dr5_2_1_a3_conflicting_plugin_assessments_survive_aggregation():
    p_support = _StaticPlugin(
        "plugin_a",
        "legal",
        {"legal"},
        lambda *a: [
            ClaimAssessment(
                claim_text="Section 230 applies",
                domain="legal",
                claim_assessment="SUPPORTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                source_dependence=DependenceConfidence.INDEPENDENT.value,
                evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
                authority_scope_note="Primary text.",
                rationale=_sample_rationale(),
            )
        ],
    )
    p_contest = _StaticPlugin(
        "plugin_b",
        "legal",
        {"legal"},
        lambda *a: [
            ClaimAssessment(
                claim_text="Section 230 applies",
                domain="legal",
                claim_assessment="CONTESTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.AMENDED_OR_SUPERSEDED.value,
                source_dependence=DependenceConfidence.UNKNOWN.value,
                evidence_classification="CONTESTED_CONFLICTING",
                authority_scope_note="Superseded in jurisdiction.",
                rationale=_sample_rationale(),
            )
        ],
    )
    reg = VerifierRegistry()
    reg.register(p_support)
    reg.register(p_contest)

    vr = verify_report_evidence(
        "- Section 230 applies.",
        [{"url": "https://congress.gov/bill", "title": "Statute", "summary": "Section 230 applies."}],
        enable_domain_verifiers=True,
        category="legal",
        registry=reg,
    )
    d = vr.to_dict()
    assert len(d["claim_assessments"]) == 2
    assert {c["plugin_name"] for c in d["claim_assessments"]} == {"plugin_a", "plugin_b"}
    assert {c["claim_assessment"] for c in d["claim_assessments"]} == {"SUPPORTED", "CONTESTED"}
    assert "section 230 applies" in d["plugin_conflicts"]


def test_dr5_2_1_a4_unknown_provenance_never_becomes_corroboration_in_registry():
    findings = [
        {
            "url": "https://blog-alpha.example.net/post1",
            "title": "Rumor Blog 1",
            "summary": "RTX 5090 features 32 GB GDDR7 memory.",
        },
        {
            "url": "https://blog-beta.example.org/post2",
            "title": "Rumor Blog 2",
            "summary": "RTX 5090 ships with 32 GB VRAM.",
        },
    ]
    vr = verify_report_evidence(
        "- RTX 5090 has 32 GB VRAM.",
        findings,
        enable_domain_verifiers=True,
        category="hardware",
        registry=build_default_registry(),
    )
    d = vr.to_dict()
    assert d["provenance_summary"]["verified_independent_origin_count"] == 0
    c = d["claim_assessments"][0]
    assert c["source_dependence"] == DependenceConfidence.UNKNOWN.value
    assert c["evidence_classification"] != "PRIMARY_CORROBORATED"


def test_dr5_2_1_a5_and_a8_immutable_evidence_and_sanitized_passages_across_plugins():
    synthetic_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhdWRpdCJ9.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    findings = [
        {
            "url": "https://www.nvidia.com/en-us/geforce/rtx-5090/",
            "title": "Official Spec",
            "summary": f"Token {synthetic_jwt} and routing # 021000021; RTX 5090 has 32 GB GDDR7.",
        }
    ]

    def _mutating_plugin(report_text, assessments, edges, provenance_summary, baseline_report):
        # Attempt to mutate frozen SourceAssessment (must fail with FrozenInstanceError / AttributeError)
        with pytest.raises((AttributeError, TypeError)):
            assessments[0].normalized_text = "TAMPERED"
        assert synthetic_jwt not in assessments[0].normalized_text
        assert "021000021" not in assessments[0].normalized_text
        return []

    reg = VerifierRegistry()
    reg.register(_StaticPlugin("mutator_probe", "hardware", {"hardware"}, _mutating_plugin))

    vr = verify_report_evidence(
        "- RTX 5090 has 32 GB VRAM.",
        findings,
        enable_domain_verifiers=True,
        category="hardware",
        registry=reg,
    )
    assert vr.plugin_errors == []


def test_dr5_2_1_a6_disabled_flag_ignores_populated_registry_and_keeps_cold_start():
    code = (
        "import sys; "
        "from src.research_verifier import verify_report_evidence; "
        "vr = verify_report_evidence('- RTX 5090 has 32 GB VRAM.', [{'url': 'https://www.nvidia.com/rtx-5090', 'title': 'NVIDIA', 'summary': 'RTX 5090 has 32 GB VRAM.'}], enable_domain_verifiers=False, category='legal', registry=object()); "
        "assert 'src.verifiers' not in sys.modules; "
        "assert 'src.research_sources' not in sys.modules; "
        "assert set(vr.to_dict().keys()) == {'verified_facts', 'corroborated_facts', 'single_source_claims', 'conflicting_evidence', 'unsupported_claims', 'unverified_citations', 'followup_queries'}; "
        "print('DR5_2_1_A6_COLD_START_OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_2_1_A6_COLD_START_OK" in proc.stdout


def test_dr5_2_1_a7_plugin_failure_timeout_and_malformed_output_isolation():
    def _raising_plugin(*args):
        raise RuntimeError("boom with sk-1234567890abcdefghij secret")

    def _malformed_plugin(*args):
        return "not-a-list-of-claim-assessments"

    def _slow_plugin(*args):
        time.sleep(0.4)
        return []

    def _healthy_plugin(*args):
        return [
            ClaimAssessment(
                claim_text="Healthy Claim",
                domain="medical",
                claim_assessment="SUPPORTED",
                publication_status=PublicationStatus.PEER_REVIEWED_VERIFIED.value,
                legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
                source_dependence=DependenceConfidence.INDEPENDENT.value,
                evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
                authority_scope_note="Verified.",
                rationale=_sample_rationale(),
            )
        ]

    reg = VerifierRegistry()
    reg.register(_StaticPlugin("p_raise", "medical", {"medical"}, _raising_plugin))
    reg.register(_StaticPlugin("p_malformed", "medical", {"medical"}, _malformed_plugin))
    reg.register(_StaticPlugin("p_slow", "medical", {"medical"}, _slow_plugin))
    reg.register(_StaticPlugin("p_healthy", "medical", {"medical"}, _healthy_plugin))

    sa = assess_source_authority({"url": "https://nih.gov/study", "title": "NIH Study", "summary": "Healthy Claim."})
    out = run_registry_verification(
        report_text="- Healthy Claim.",
        assessments=[sa],
        edges=[],
        provenance_summary={"verified_independent_origins": [sa.url], "unverified_independence_urls": []},
        baseline_report=None,
        category="medical",
        registry=reg,
        plugin_timeout_seconds=0.1,
    )
    # Healthy plugin assessment preserved
    assert len(out["claim_assessments"]) == 1
    assert out["claim_assessments"][0]["plugin_name"] == "p_healthy"
    # All 3 failing plugins recorded with sanitized error info
    err_by_name = {e["plugin_name"]: e for e in out["plugin_errors"]}
    assert set(err_by_name.keys()) == {"p_raise", "p_malformed", "p_slow"}
    assert "sk-1234567890abcdefghij" not in err_by_name["p_raise"]["detail"]
    assert "[REDACTED_CREDENTIAL]" in err_by_name["p_raise"]["detail"]
    assert err_by_name["p_slow"]["error_type"] == "TimeoutError"


def test_dr5_2_1_a9_deterministic_aggregation_independent_of_registration_order():
    ca_one = ClaimAssessment(
        claim_text="Claim Z",
        domain="historical",
        claim_assessment="SUPPORTED",
        publication_status=PublicationStatus.NOT_APPLICABLE.value,
        legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
        source_dependence=DependenceConfidence.INDEPENDENT.value,
        evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
        authority_scope_note="Archive A.",
        rationale=_sample_rationale(),
    )
    ca_two = ClaimAssessment(
        claim_text="Claim A",
        domain="historical",
        claim_assessment="CONTESTED",
        publication_status=PublicationStatus.NOT_APPLICABLE.value,
        legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
        source_dependence=DependenceConfidence.UNKNOWN.value,
        evidence_classification="CONTESTED_CONFLICTING",
        authority_scope_note="Archive B.",
        rationale=_sample_rationale(),
    )

    p_first = _StaticPlugin("zeta_plugin", "historical", {"historical"}, lambda *a: [ca_one])
    p_second = _StaticPlugin("alpha_plugin", "historical", {"historical"}, lambda *a: [ca_two])

    reg_forward = VerifierRegistry()
    reg_forward.register(p_first)
    reg_forward.register(p_second)

    reg_reverse = VerifierRegistry()
    reg_reverse.register(p_second)
    reg_reverse.register(p_first)

    findings = [{"url": "https://archives.gov/doc", "title": "Archive", "summary": "Claim A and Claim Z."}]
    d1 = verify_report_evidence("- Claim A.\n- Claim Z.", findings, enable_domain_verifiers=True, category="historical", registry=reg_forward).to_dict()
    d2 = verify_report_evidence("- Claim A.\n- Claim Z.", findings, enable_domain_verifiers=True, category="historical", registry=reg_reverse).to_dict()

    assert d1 == d2
    assert [c["claim_text"] for c in d1["claim_assessments"]] == ["Claim A", "Claim Z"]
