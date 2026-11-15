"""DR-5.2.2 Targeted Audit Tests: Non-Blocking Timeout Lifecycle, Deep Immutability & Conflict Preservation."""

from __future__ import annotations

import subprocess
import sys

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
    freeze_evidence_snapshot,
    run_registry_verification,
)


class _StaticPlugin:
    def __init__(self, name: str, domain: str, categories: set[str], fn):
        self.name = name
        self.domain = domain
        self._categories = categories
        self._fn = fn

    def supports_category(self, category: str) -> bool:
        return category in self._categories

    def verify(self, report_text, assessments, edges, provenance_summary, baseline_report):
        return self._fn(report_text, assessments, edges, provenance_summary, baseline_report)


def _base_rationale() -> dict[str, str]:
    return {
        "source_authority": "PRIMARY_INSTITUTIONAL",
        "independence": "Evaluated per evidence relationship",
        "directness": "DIRECT",
        "contradictions": "Preserved",
        "evidence_completeness": "Complete",
    }


def test_dr5_2_2_b1_nonblocking_timeout_lifecycle_and_late_fencing_in_subprocess():
    """Verify in a fresh subprocess that a 2.0s hung plugin returns in <0.35s with TIMED_OUT_BACKGROUND_THREAD_ORPHANED."""
    code = (
        "import threading, time\n"
        "from src.research_sources import assess_source_authority\n"
        "from src.verifiers.base import ClaimAssessment, VerifierRegistry, run_registry_verification, get_active_orphaned_thread_count\n"
        "release_evt = threading.Event()\n"
        "late_mutated = []\n"
        "class HungPlugin:\n"
        "    name = 'hung_plugin'\n"
        "    domain = 'legal'\n"
        "    def supports_category(self, c): return True\n"
        "    def verify(self, *args):\n"
        "        release_evt.wait(timeout=2.0)\n"
        "        late_mutated.append('LATE_WRITE')\n"
        "        return [ClaimAssessment(claim_text='Late Claim', domain='legal', claim_assessment='SUPPORTED', publication_status='NOT_APPLICABLE', legal_currency='CURRENT_VERIFIED', source_dependence='INDEPENDENT', evidence_classification='SINGLE_SOURCE_AUTHORITATIVE', authority_scope_note='Late')]\n"
        "class FastPlugin:\n"
        "    name = 'fast_plugin'\n"
        "    domain = 'legal'\n"
        "    def supports_category(self, c): return True\n"
        "    def verify(self, *args):\n"
        "        return [ClaimAssessment(claim_text='Fast Claim', domain='legal', claim_assessment='SUPPORTED', publication_status='NOT_APPLICABLE', legal_currency='CURRENT_VERIFIED', source_dependence='INDEPENDENT', evidence_classification='SINGLE_SOURCE_AUTHORITATIVE', authority_scope_note='Fast')]\n"
        "reg = VerifierRegistry()\n"
        "reg.register(HungPlugin())\n"
        "reg.register(FastPlugin())\n"
        "sa = assess_source_authority({'url': 'https://congress.gov/act', 'title': 'Act', 'summary': 'Fast Claim.'})\n"
        "t0 = time.perf_counter()\n"
        "out = run_registry_verification(report_text='- Fast Claim.', assessments=[sa], edges=[], provenance_summary={'verified_independent_origins': [sa.url], 'unverified_independence_urls': []}, baseline_report=None, category='legal', registry=reg, plugin_timeout_seconds=0.05)\n"
        "elapsed = time.perf_counter() - t0\n"
        "assert elapsed < 0.35, f'Blocked too long: {elapsed}'\n"
        "assert len(out['claim_assessments']) == 1 and out['claim_assessments'][0]['plugin_name'] == 'fast_plugin'\n"
        "assert len(out['plugin_errors']) == 1\n"
        "err = out['plugin_errors'][0]\n"
        "assert err['status'] == 'TIMED_OUT_BACKGROUND_THREAD_ORPHANED'\n"
        "assert err['technical_debt_id'] == 'PROCESS_ISOLATION_DEFERRED_FOR_UNTRUSTED_PLUGINS'\n"
        "assert get_active_orphaned_thread_count() == 1\n"
        "release_evt.set()\n"
        "time.sleep(0.05)\n"
        "assert len(out['claim_assessments']) == 1, 'Orphaned thread injected late result!'\n"
        "assert get_active_orphaned_thread_count() == 0\n"
        "print('DR5_2_2_B1_SUBPROCESS_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_2_2_B1_SUBPROCESS_OK" in proc.stdout


def test_dr5_2_2_b2_recursive_deep_immutability_and_post_return_mutation_isolation():
    sa = assess_source_authority({
        "url": "https://www.nvidia.com/en-us/geforce/rtx-5090/",
        "title": "Official Spec",
        "summary": "RTX 5090 features 32 GB GDDR7 memory.",
    })
    nested_summary = {
        "verified_independent_origins": [sa.url],
        "unverified_independence_urls": [],
        "nested_meta": {"tags": ["primary", "spec"], "flags": {"strict"}},
    }
    _, _, frozen_sum, _ = freeze_evidence_snapshot([sa], [], nested_summary)
    with pytest.raises(TypeError):
        frozen_sum["nested_meta"]["tags"] = ["tampered"]
    assert isinstance(frozen_sum["nested_meta"]["tags"], tuple)
    assert isinstance(frozen_sum["nested_meta"]["flags"], frozenset)

    retained_rationale = _base_rationale()
    retained_passage = build_passage_evidence_ref(sa, query_or_claim_term="32 GB GDDR7")
    retained_list = [retained_passage]

    def _retaining_plugin(*args):
        return [
            ClaimAssessment(
                claim_text="RTX 5090: 32 GB",
                domain="hardware",
                claim_assessment="SUPPORTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
                source_dependence=DependenceConfidence.INDEPENDENT.value,
                evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
                authority_scope_note="Official.",
                supporting_passages=retained_list,
                rationale=retained_rationale,
            )
        ]

    reg = VerifierRegistry()
    reg.register(_StaticPlugin("retaining_plugin", "hardware", {"hardware"}, _retaining_plugin))
    out = run_registry_verification(
        report_text="- RTX 5090 has 32 GB VRAM.",
        assessments=[sa],
        edges=[],
        provenance_summary=nested_summary,
        baseline_report=None,
        category="hardware",
        registry=reg,
    )

    retained_rationale["source_authority"] = "MUTATED_POST_RETURN"
    retained_passage.excerpt = "MUTATED_EXCERPT"
    retained_list.clear()

    accepted = out["claim_assessments"][0]
    assert accepted["rationale"]["source_authority"] == "PRIMARY_INSTITUTIONAL"
    assert len(accepted["supporting_passages"]) == 1
    assert "32 GB GDDR7" in accepted["supporting_passages"][0]["excerpt"]


def test_dr5_2_2_b3_conflict_aggregation_preserves_2_to_1_split_without_majority_consensus():
    sa = assess_source_authority({
        "url": "https://congress.gov/statute",
        "title": "Statute Text",
        "summary": "Section 230 immunity provision.",
    })
    p_ref = build_passage_evidence_ref(sa, query_or_claim_term="Section 230")

    def _make_supporter(name: str):
        return _StaticPlugin(
            name,
            "legal",
            {"legal"},
            lambda *a: [
                ClaimAssessment(
                    claim_text="Section 230 applies",
                    domain="legal",
                    claim_assessment="SUPPORTED",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_VERIFIED.value,
                    source_dependence=DependenceConfidence.INDEPENDENT.value,
                    evidence_classification="SINGLE_SOURCE_AUTHORITATIVE",
                    authority_scope_note="Primary statute.",
                    supporting_passages=[p_ref],
                    rationale=_base_rationale(),
                )
            ],
        )

    p_contester = _StaticPlugin(
        "plugin_c_contester",
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
                authority_scope_note="Subsequent amendment.",
                conflicting_passages=[p_ref],
                rationale=_base_rationale(),
            )
        ],
    )

    reg = VerifierRegistry()
    reg.register(_make_supporter("plugin_a_supporter"))
    reg.register(_make_supporter("plugin_b_supporter"))
    reg.register(p_contester)

    vr = verify_report_evidence(
        "- Section 230 applies.",
        [{"url": "https://congress.gov/statute", "title": "Statute Text", "summary": "Section 230 immunity provision."}],
        enable_domain_verifiers=True,
        category="legal",
        registry=reg,
    )
    d = vr.to_dict()
    assert len(d["claim_assessments"]) == 3
    assert len(d["conflict_records"]) == 1
    rec = d["conflict_records"][0]
    assert rec["normalized_claim_id"] == "section 230 applies"
    assert rec["resolution_policy"] == "PRESERVE_CONFLICT_NO_CONSENSUS"
    assert rec["competing_states"] == ["CONTESTED", "SUPPORTED"]
    assert len(rec["competing_assessments"]) == 3
    assert {a["plugin_name"] for a in rec["competing_assessments"]} == {
        "plugin_a_supporter",
        "plugin_b_supporter",
        "plugin_c_contester",
    }


def test_dr5_2_2_b4_cold_start_and_dr4_2_baseline_unchanged():
    code = (
        "import sys\n"
        "from src.deep_research import DeepResearcher\n"
        "from src.research_verifier import verify_report_evidence\n"
        "r = DeepResearcher(llm_endpoint='http://127.0.0.1:11434/v1', llm_model='gemma4:31b')\n"
        "assert r.enable_domain_verifiers is False\n"
        "vr = verify_report_evidence('- RTX 5090 has 32 GB VRAM.', [{'url': 'https://www.nvidia.com/rtx-5090', 'title': 'NVIDIA', 'summary': 'RTX 5090 has 32 GB VRAM.'}], enable_domain_verifiers=False, category='legal', registry=object())\n"
        "assert 'src.verifiers' not in sys.modules\n"
        "assert 'src.research_sources' not in sys.modules\n"
        "assert 'conflict_records' not in vr.to_dict()\n"
        "print('DR5_2_2_B4_COLD_START_OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    assert "DR5_2_2_B4_COLD_START_OK" in proc.stdout
