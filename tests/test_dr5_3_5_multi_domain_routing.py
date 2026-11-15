"""DR-5.3.5 Multi-Domain Claim Routing & Cross-Plugin Conflict Aggregation Tests (30 tests: R1-R10, A1-A20)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any, List, Mapping, Sequence

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PublicationStatus,
    assess_source_authority,
    build_passage_evidence_ref,
    build_provenance_graph,
)
from src.research_verifier import VerificationReport, verify_report_evidence
from src.verifiers.base import (
    ClaimAssessment,
    HardwareBaselinePlugin,
    VerifierRegistry,
    build_default_registry,
    run_registry_verification,
)
from src.verifiers.router import (
    ROUTER_VERSION,
    SUPPORTED_ROUTING_DOMAINS,
    aggregate_cross_plugin_conflicts,
    classify_claim_domains,
    extract_canonical_claims,
    normalize_claim_key,
    route_claims_to_plugins,
)


def _make_finding(url: str, title: str, text: str) -> dict[str, Any]:
    return {"url": url, "title": title, "content": text, "snippet": text}


class _StubDomainPlugin:
    def __init__(
        self,
        name: str,
        domain: str,
        verdict: str = "SUPPORTED",
        *,
        delay_s: float = 0.0,
        raise_exc: Exception | None = None,
        mutate_attempt: bool = False,
        target_substring: str = "",
    ) -> None:
        self.name = name
        self.domain = domain
        self._verdict = verdict
        self._delay_s = delay_s
        self._raise_exc = raise_exc
        self._mutate_attempt = mutate_attempt
        self._target_substring = target_substring

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() == self.domain

    def verify(
        self,
        report_text: str,
        assessments: Sequence[Any],
        edges: Sequence[Any],
        provenance_summary: Mapping[str, Any],
        baseline_report: Any,
    ) -> List[ClaimAssessment]:
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._mutate_attempt:
            assessments[0].normalized_text = "MUTATED"  # type: ignore[misc]
        out: List[ClaimAssessment] = []
        for cl in extract_canonical_claims(report_text, baseline_report=baseline_report):
            if self.domain not in cl.matched_domains and self.domain != "general":
                continue
            if self._target_substring and self._target_substring.lower() not in cl.original_claim_text.lower():
                continue
            passages = []
            if assessments:
                passages.append(
                    build_passage_evidence_ref(
                        assessments[0],
                        query_or_claim_term=cl.original_claim_text[:40],
                    )
                )
            out.append(
                ClaimAssessment(
                    claim_text=cl.original_claim_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment=self._verdict,
                    publication_status=PublicationStatus.PEER_REVIEWED_VERIFIED.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_VERIFIED.value,
                    source_dependence=DependenceConfidence.INDEPENDENT.value,
                    evidence_classification=f"{self.domain.upper()}_{self._verdict}",
                    authority_scope_note=f"Evaluated by {self.name}",
                    supporting_passages=passages if self._verdict == "SUPPORTED" else [],
                    conflicting_passages=passages if self._verdict == "CONTESTED" else [],
                    rationale={"plugin": self.name, "domain": self.domain},
                )
            )
        return out


# ==============================================================================
# R1 - R10: MANDATORY SAFEGUARD & ROUTING INVARIANTS
# ==============================================================================


def test_R1_preserves_canonical_claim_identity_tuple():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion.\nFDA Phase 3 randomized clinical trial showed 68% efficacy."
    claims = extract_canonical_claims(report)
    assert len(claims) == 2
    assert (claims[0].claim_index, claims[0].original_claim_text, claims[0].normalized_claim_key) == (
        0,
        "Acme Corp Q4 2025 revenue was $12.4 billion.",
        "acme corp q4 2025 revenue was $12.4 billion.",
    )
    assert (claims[1].claim_index, claims[1].original_claim_text, claims[1].normalized_claim_key) == (
        1,
        "FDA Phase 3 randomized clinical trial showed 68% efficacy.",
        "fda phase 3 randomized clinical trial showed 68% efficacy.",
    )


def test_R2_never_merges_separate_claims_with_identical_normalized_wording():
    report = (
        "- Acme Corp Q4 2025 revenue was $12.4 billion.\n"
        "* ACME CORP Q4 2025 REVENUE WAS $12.4 BILLION."
    )
    claims = extract_canonical_claims(report)
    assert len(claims) == 2
    assert claims[0].claim_index == 0
    assert claims[1].claim_index == 1
    assert claims[0].normalized_claim_key == claims[1].normalized_claim_key
    assert claims[0].original_claim_text != claims[1].original_claim_text


def test_R3_dispatches_overlapping_multi_domain_claims_to_every_applicable_plugin():
    claim_text = (
        "Under 21 U.S.C. 355 statute, Pfizer Phase 3 clinical trial drug reported $4.2 billion Q4 2025 revenue."
    )
    domains = classify_claim_domains(claim_text)
    assert {"financial", "legal", "medical"} <= set(domains)

    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_stub", "financial", "SUPPORTED"))
    reg.register(_StubDomainPlugin("leg_stub", "legal", "SUPPORTED"))
    reg.register(_StubDomainPlugin("med_stub", "medical", "SUPPORTED"))

    findings = [_make_finding("https://sec.gov/pfizer-10k", "Pfizer 10-K", claim_text)]
    vr = verify_report_evidence(
        claim_text,
        findings,
        enable_domain_verifiers=True,
        category="general",
        registry=reg,
    )
    assert vr.claim_assessments is not None
    plugins_ran = {ca["plugin_name"] for ca in vr.claim_assessments}
    assert plugins_ran == {"fin_stub", "leg_stub", "med_stub"}


def test_R4_preserves_hardware_baseline_plugin_baseline_report_contract():
    report = "The RTX 4090 features 24 GB VRAM and 450 W TDP [TechSpec](https://vendor.example.com/rtx4090)."
    findings = [
        _make_finding(
            "https://vendor.example.com/rtx4090",
            "RTX 4090 Official Specs",
            "RTX 4090 includes 24 GB GDDR6X VRAM and 450 W TDP.",
        )
    ]
    vr = verify_report_evidence(
        report,
        findings,
        enable_domain_verifiers=True,
        category="hardware",
    )
    assert vr.claim_assessments is not None
    hw_items = [ca for ca in vr.claim_assessments if ca["plugin_name"] == "hardware_baseline"]
    assert len(hw_items) >= 2
    assert all(ca["domain"] == "hardware" for ca in hw_items)


def test_R5_maintains_preserve_conflict_no_consensus_across_plugins():
    claim_text = "Acme Corp Q4 2025 revenue was $12.4 billion under SEC Rule 10b-5 compliance."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_stub", "financial", "SUPPORTED"))
    reg.register(_StubDomainPlugin("leg_stub", "legal", "CONTESTED"))

    findings = [_make_finding("https://sec.gov/acme", "Acme Filing", claim_text)]
    vr = verify_report_evidence(
        claim_text,
        findings,
        enable_domain_verifiers=True,
        category="general",
        registry=reg,
    )
    assert vr.conflict_records is not None
    assert len(vr.conflict_records) == 1
    rec = vr.conflict_records[0]
    assert rec["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS"
    assert rec["competing_states"] == ["CONTESTED", "SUPPORTED"]
    assert rec["is_cross_domain_conflict"] is True
    assert set(rec["domains_involved"]) == {"financial", "legal"}


def test_R6_isolates_plugin_exceptions_and_timeouts():
    claim_text = "Acme Corp Q4 2025 revenue was $12.4 billion under SEC Rule 10b-5 compliance."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_ok", "financial", "SUPPORTED"))
    reg.register(_StubDomainPlugin("leg_boom", "legal", raise_exc=RuntimeError("boom_secret_token=sk-1234567890abcdef")))
    reg.register(_StubDomainPlugin("med_slow", "medical", delay_s=0.35))

    claim_with_med = claim_text + " Phase 3 clinical trial patients."
    findings = [_make_finding("https://sec.gov/acme", "Acme Filing", claim_with_med)]
    assessments = [assess_source_authority(f, is_verified_fetched_url=True) for f in findings]
    edges, summary = build_provenance_graph(assessments)

    out = run_registry_verification(
        report_text=claim_with_med,
        assessments=assessments,
        edges=edges,
        provenance_summary=summary,
        baseline_report=VerificationReport(),
        category="general",
        registry=reg,
        plugin_timeout_seconds=0.05,
    )
    assert any(ca["plugin_name"] == "fin_ok" for ca in out["claim_assessments"])
    err_statuses = {e["plugin_name"]: e["status"] for e in out["plugin_errors"]}
    assert err_statuses["leg_boom"] == "PLUGIN_FAILED_CLOSED"
    assert err_statuses["med_slow"] == "TIMED_OUT_BACKGROUND_THREAD_ORPHANED"
    assert "sk-1234567890abcdef" not in json.dumps(out["plugin_errors"])


def test_R7_deterministic_output_regardless_of_registration_order():
    claim_text = "Under 21 U.S.C. 355 statute, Pfizer Phase 3 clinical trial reported $4.2 billion Q4 2025 revenue."
    findings = [_make_finding("https://sec.gov/pfizer", "Pfizer Filing", claim_text)]

    plugins = [
        _StubDomainPlugin("z_med", "medical", "CONTESTED"),
        _StubDomainPlugin("a_fin", "financial", "SUPPORTED"),
        _StubDomainPlugin("m_leg", "legal", "INSUFFICIENT_EVIDENCE"),
    ]

    reg_forward = VerifierRegistry()
    for p in plugins:
        reg_forward.register(p)

    reg_reverse = VerifierRegistry()
    for p in reversed(plugins):
        reg_reverse.register(p)

    def _strip_ts(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: ("<TS>" if k == "retrieval_timestamp" else _strip_ts(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_strip_ts(v) for v in obj]
        return obj

    vr1 = _strip_ts(verify_report_evidence(claim_text, findings, enable_domain_verifiers=True, registry=reg_forward).to_dict())
    vr2 = _strip_ts(verify_report_evidence(claim_text, findings, enable_domain_verifiers=True, registry=reg_reverse).to_dict())
    assert vr1 == vr2


def test_R8_records_missing_domains_via_missing_domain_plugin_and_missing_domain_domains():
    report = "In 44 BCE, the Roman Senate treaty of antiquity reshaped Mediterranean history."
    findings = [_make_finding("https://archive.example.org/rome", "Roman Archive", report)]
    reg_without_hist = VerifierRegistry()
    reg_without_hist.register(HardwareBaselinePlugin())
    vr = verify_report_evidence(
        report,
        findings,
        enable_domain_verifiers=True,
        category="general",
        registry=reg_without_hist,
    )
    assert vr.missing_domain_plugin is True
    assert vr.missing_domain_domains == ["historical"]
    assert "historical_verifier" in build_default_registry().list_plugins()


def test_R9_preserves_lazy_imports_when_disabled():
    code = (
        "import sys; "
        "from src.research_verifier import verify_report_evidence; "
        "vr = verify_report_evidence('RTX 4090 has 24 GB VRAM', [{'url':'https://a.test','content':'RTX 4090 has 24 GB VRAM'}]); "
        "assert 'src.verifiers.router' not in sys.modules, 'router eagerly imported'; "
        "assert 'src.verifiers.base' not in sys.modules, 'base eagerly imported'; "
        "assert vr.claim_assessments is None; "
        "print('LAZY_OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    assert "LAZY_OK" in proc.stdout


def test_R10_keeps_enable_domain_verifiers_false_as_strict_default():
    import dataclasses
    import inspect
    import src.deep_research as dr_mod

    sig_vr = inspect.signature(verify_report_evidence)
    assert sig_vr.parameters["enable_domain_verifiers"].default is False

    found_in_dr = False
    for obj in vars(dr_mod).values():
        if getattr(obj, "__module__", "") != "src.deep_research":
            continue
        if dataclasses.is_dataclass(obj):
            for f in dataclasses.fields(obj):
                if f.name == "enable_domain_verifiers":
                    assert f.default is False
                    found_in_dr = True
        elif inspect.isfunction(obj):
            params = inspect.signature(obj).parameters
            if "enable_domain_verifiers" in params:
                assert params["enable_domain_verifiers"].default is False
                found_in_dr = True
        elif inspect.isclass(obj):
            for meth in vars(obj).values():
                if inspect.isfunction(meth):
                    params = inspect.signature(meth).parameters
                    if "enable_domain_verifiers" in params:
                        assert params["enable_domain_verifiers"].default is False
                        found_in_dr = True
    assert found_in_dr, "Expected enable_domain_verifiers=False in src.deep_research" 


# ==============================================================================
# A1 - A20: ADVERSARIAL, MULTI-DOMAIN & CROSS-PLUGIN EDGE CASES
# ==============================================================================


def test_A1_all_five_routing_domains_recognized():
    assert SUPPORTED_ROUTING_DOMAINS == ("financial", "legal", "medical", "historical", "hardware")
    assert classify_claim_domains("Acme FY2025 revenue reached $5.1 billion.") == ("financial",)
    assert classify_claim_domains("Section 230 statute governs platform liability.") == ("legal",)
    assert classify_claim_domains("Phase 3 randomized clinical trial measured patient survival.") == ("medical",)
    assert classify_claim_domains("The Treaty of Westphalia in 1648 CE ended the war.") == ("historical",)
    assert classify_claim_domains("Ryzen 9 7950X has 16 cores and 170 W TDP.") == ("hardware",)


def test_A2_default_registry_contains_exactly_five_authorized_plugins():
    reg = build_default_registry()
    assert reg.list_plugins() == [
        "financial_verifier",
        "hardware_baseline",
        "historical_verifier",
        "legal_verifier",
        "medical_verifier",
    ]


def test_A3_empty_report_returns_clean_empty_routing_structures():
    vr = verify_report_evidence("", [], enable_domain_verifiers=True, category="general")
    assert vr.to_dict() == {
        "verified_facts": [],
        "corroborated_facts": [],
        "single_source_claims": [],
        "conflicting_evidence": [],
        "unsupported_claims": [],
        "unverified_citations": [],
        "followup_queries": [],
    }


def test_A4_markdown_heading_and_table_separator_lines_excluded_from_claims():
    report = (
        "# Executive Summary\n"
        "| Model | VRAM |\n"
        "| --- | --- |\n"
        "| RTX 4090 | 24 GB |\n"
    )
    claims = extract_canonical_claims(report)
    assert len(claims) == 2
    assert claims[0].original_claim_text == "| Model | VRAM |"
    assert claims[1].original_claim_text == "| RTX 4090 | 24 GB |"


def test_A5_duplicate_claims_with_different_verdicts_keep_separate_claim_indices():
    report = (
        "- Acme Q4 2025 revenue was $10 billion.\n"
        "- Acme Q4 2025 revenue was $10 billion."
    )
    claims = extract_canonical_claims(report)
    assert len(claims) == 2
    a0 = ClaimAssessment(
        claim_text=claims[0].original_claim_text,
        domain="financial",
        plugin_name="p1",
        claim_assessment="SUPPORTED",
        publication_status=PublicationStatus.NOT_APPLICABLE.value,
        legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
        source_dependence=DependenceConfidence.INDEPENDENT.value,
        evidence_classification="OK",
        authority_scope_note="n/a",
        rationale={"claim_index": "0"},
    )
    a1 = ClaimAssessment(
        claim_text=claims[1].original_claim_text,
        domain="financial",
        plugin_name="p2",
        claim_assessment="CONTESTED",
        publication_status=PublicationStatus.NOT_APPLICABLE.value,
        legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
        source_dependence=DependenceConfidence.UNKNOWN.value,
        evidence_classification="CONFLICT",
        authority_scope_note="n/a",
        rationale={"claim_index": "1"},
    )
    _, conflicts = aggregate_cross_plugin_conflicts([a0, a1], claims)
    assert len(conflicts) == 1
    assert conflicts[0]["claim_index"] == 1


def test_A6_same_claim_index_evaluated_by_three_plugins_preserves_all_three_in_conflict_record():
    report = "Under 21 U.S.C. 355 statute, Pfizer Phase 3 clinical trial reported $4.2 billion Q4 2025 revenue."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("p_fin", "financial", "SUPPORTED"))
    reg.register(_StubDomainPlugin("p_leg", "legal", "CONTESTED"))
    reg.register(_StubDomainPlugin("p_med", "medical", "INSUFFICIENT_EVIDENCE"))

    findings = [_make_finding("https://sec.gov/pfizer", "Pfizer 10-K", report)]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr.conflict_records is not None
    assert len(vr.conflict_records) == 1
    rec = vr.conflict_records[0]
    assert rec["claim_index"] == 0
    assert rec["competing_states"] == ["CONTESTED", "INSUFFICIENT_EVIDENCE", "SUPPORTED"]
    assert len(rec["competing_assessments"]) == 3


def test_A7_plugin_cannot_mutate_frozen_source_views_during_routed_execution():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("mutator", "financial", mutate_attempt=True))
    findings = [_make_finding("https://sec.gov/acme", "Acme 10-K", report)]

    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr.plugin_errors is not None
    assert len(vr.plugin_errors) == 1
    assert vr.plugin_errors[0]["status"] == "PLUGIN_FAILED_CLOSED"


def test_A8_unregistered_custom_category_sets_missing_domain_plugin_and_domains():
    report = "In 1789 CE, the historical assembly adopted the declaration."
    reg = VerifierRegistry()
    reg.register(HardwareBaselinePlugin())
    findings = [_make_finding("https://archive.example.org/1789", "Archive 1789", report)]

    vr = verify_report_evidence(
        report,
        findings,
        enable_domain_verifiers=True,
        category="historical",
        registry=reg,
    )
    assert vr.missing_domain_plugin is True
    assert vr.missing_domain_domains == ["historical"]


def test_A9_all_routed_domains_covered_sets_missing_domain_plugin_false():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_stub", "financial", "SUPPORTED"))
    findings = [_make_finding("https://sec.gov/acme", "Acme 10-K", report)]

    vr = verify_report_evidence(
        report,
        findings,
        enable_domain_verifiers=True,
        category="financial",
        registry=reg,
    )
    assert vr.missing_domain_plugin is False
    assert vr.missing_domain_domains == []


def test_A10_routed_claims_metadata_recorded_in_verification_report():
    report = "Acme Q4 2025 revenue was $12.4 billion.\nRTX 4090 has 24 GB VRAM."
    findings = [_make_finding("https://example.test/doc", "Doc", report)]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True)
    assert vr.routed_claims is not None
    assert len(vr.routed_claims) == 3
    assert vr.routed_claims[0]["matched_domains"] == ["financial"]
    assert vr.routed_claims[1]["matched_domains"] == ["hardware"]
    assert vr.routed_claims[2]["original_claim_text"] == "RTX 4090: 24 GB"
    assert vr.routed_claims[2]["matched_domains"] == ["hardware"]


def test_A11_disabled_mode_to_dict_has_zero_new_keys():
    report = "RTX 4090 has 24 GB VRAM."
    findings = [_make_finding("https://example.test/rtx", "RTX", report)]
    d = verify_report_evidence(report, findings, enable_domain_verifiers=False).to_dict()
    assert set(d.keys()) == {
        "verified_facts",
        "corroborated_facts",
        "single_source_claims",
        "conflicting_evidence",
        "unsupported_claims",
        "unverified_citations",
        "followup_queries",
    }


def test_A12_real_financial_and_medical_plugins_coexist_on_mixed_report():
    report = (
        "Acme Corp FY2025 revenue was $9.5 billion.\n"
        "Phase 3 randomized controlled clinical trial evaluated 400 patients at 50 mg dosage."
    )
    findings = [
        _make_finding(
            "https://sec.gov/acme-10k",
            "Acme FY2025 10-K",
            "Acme Corp FY2025 revenue was $9.5 billion.",
        ),
        _make_finding(
            "https://pubmed.ncbi.nlm.nih.gov/999999",
            "Phase 3 Trial",
            "Phase 3 randomized controlled clinical trial evaluated 400 patients at 50 mg dosage.",
        ),
    ]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, category="general")
    assert vr.claim_assessments is not None
    domains = {ca["domain"] for ca in vr.claim_assessments}
    assert "financial" in domains
    assert "medical" in domains


def test_A13_normalize_claim_key_strips_markdown_links_and_bullets():
    k1 = normalize_claim_key("- [Acme 10-K](https://sec.gov/acme) revenue was $12.4 billion!")
    k2 = normalize_claim_key("* Acme 10-K revenue was $12.4 billion")
    assert k1 == k2 == "acme 10-k revenue was $12.4 billion"


def test_A14_unverified_independence_downgrade_preserved_through_router():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_stub", "financial", "SUPPORTED"))
    findings = [_make_finding("https://blog.example.org/acme", "Blog", report)]
    assessments = [assess_source_authority(f, is_verified_fetched_url=True) for f in findings]
    edges, summary = build_provenance_graph(assessments)
    summary["unverified_independence_urls"] = ["https://blog.example.org/acme"]

    out = route_claims_to_plugins(
        report_text=report,
        assessments=assessments,
        edges=edges,
        provenance_summary=summary,
        baseline_report=VerificationReport(),
        category="general",
        registry=reg,
        plugin_timeout_seconds=2.0,
    )
    assert out["claim_assessments"][0]["source_dependence"] == DependenceConfidence.UNKNOWN.value


def test_A15_pii_tokens_in_plugin_exception_detail_are_sanitized():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    reg = VerifierRegistry()
    reg.register(
        _StubDomainPlugin(
            "leaky_plugin",
            "financial",
            raise_exc=ValueError("failed with Bearer sk-proj-999988887777666655554444"),
        )
    )
    findings = [_make_finding("https://sec.gov/acme", "Acme", report)]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr.plugin_errors is not None
    assert "999988887777666655554444" not in vr.plugin_errors[0]["detail"]


def test_A16_hardware_conflict_in_baseline_report_flows_into_conflict_records():
    report = "RTX 4090 has 24 GB VRAM."
    findings = [
        _make_finding("https://src1.example.com/a", "Src1", "RTX 4090 has 24 GB VRAM."),
        _make_finding("https://src2.example.com/b", "Src2", "RTX 4090 has 16 GB VRAM."),
    ]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, category="hardware")
    assert vr.conflicting_evidence
    assert vr.conflict_records is not None
    assert any(r["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS" for r in vr.conflict_records)


def test_A17_non_list_plugin_return_value_fails_closed():
    class _BadReturnPlugin:
        name = "bad_return"
        domain = "financial"

        def supports_category(self, category: str) -> bool:
            return category == "financial"

        def verify(self, *args: Any, **kwargs: Any) -> Any:
            return {"not": "a list"}

    reg = VerifierRegistry()
    reg.register(_BadReturnPlugin())  # type: ignore[arg-type]
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    findings = [_make_finding("https://sec.gov/acme", "Acme", report)]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr.plugin_errors is not None
    assert vr.plugin_errors[0]["status"] == "PLUGIN_FAILED_CLOSED"


def test_A18_invalid_claim_assessment_verdict_string_rejected_fail_closed():
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("bad_verdict", "financial", verdict="DEFINITELY_TRUE"))
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    findings = [_make_finding("https://sec.gov/acme", "Acme", report)]
    vr = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr.plugin_errors is not None
    assert vr.plugin_errors[0]["status"] == "PLUGIN_FAILED_CLOSED"


def test_A19_post_return_mutation_of_verification_report_does_not_corrupt_fresh_runs():
    report = "Acme Corp Q4 2025 revenue was $12.4 billion."
    reg = VerifierRegistry()
    reg.register(_StubDomainPlugin("fin_stub", "financial", "SUPPORTED"))
    findings = [_make_finding("https://sec.gov/acme", "Acme", report)]

    vr1 = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr1.claim_assessments is not None
    vr1.claim_assessments[0]["rationale"]["injected"] = "evil"

    vr2 = verify_report_evidence(report, findings, enable_domain_verifiers=True, registry=reg)
    assert vr2.claim_assessments is not None
    assert "injected" not in vr2.claim_assessments[0]["rationale"]


def test_A20_router_version_constant_exposed():
    assert ROUTER_VERSION == "dr5.3.5-v1.0"
