"""DR-5.2 / DR-5.2.1 / DR-5.2.2 Modular Verifier Registry & Decoupled Four-Axis ClaimAssessment.

Enforces architectural audit invariants DR5.2.1-A1..A9 and DR5.2.2-B1..B4:
  - B1: Non-blocking timeout handling (executor.shutdown(wait=False)), explicit
        TIMED_OUT_BACKGROUND_THREAD_ORPHANED status, atomic late-result fencing,
        bounded orphaned-thread circuit breaker, and documented technical debt
        PROCESS_ISOLATION_DEFERRED_FOR_UNTRUSTED_PLUGINS.
  - B2: Recursive deep immutability across nested dicts/lists/sets and frozen
        baseline_report views, plus deep-copy post-return isolation so retained
        references cannot mutate accepted assessments.
  - B3: Explicit structured conflict_records with resolution_policy =
        PRESERVE_CONFLICT_NO_CONSENSUS (never majority vote or last-plugin-wins).
  - B4: Exact DR-4.2 baseline and cold-start isolation when disabled.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PassageEvidenceRef,
    ProvenanceEdge,
    PublicationStatus,
    SourceAssessment,
    SourceClass,
    build_passage_evidence_ref,
    sanitize_text_full,
)

REGISTRY_VERSION = "dr5.2.2-v3.1"
DEFAULT_PLUGIN_TIMEOUT_SECONDS = 5.0
MAX_ORPHANED_PLUGIN_THREADS = 4
THREAD_TIMEOUT_TECH_DEBT_ID = "PROCESS_ISOLATION_DEFERRED_FOR_UNTRUSTED_PLUGINS"

_ORPHAN_LOCK = threading.Lock()
_ACTIVE_ORPHANED_THREADS = 0


def get_active_orphaned_thread_count() -> int:
    with _ORPHAN_LOCK:
        return _ACTIVE_ORPHANED_THREADS


def _increment_orphaned_threads() -> None:
    global _ACTIVE_ORPHANED_THREADS
    with _ORPHAN_LOCK:
        _ACTIVE_ORPHANED_THREADS += 1


def _decrement_orphaned_threads() -> None:
    global _ACTIVE_ORPHANED_THREADS
    with _ORPHAN_LOCK:
        if _ACTIVE_ORPHANED_THREADS > 0:
            _ACTIVE_ORPHANED_THREADS -= 1


def _deep_freeze(obj: Any) -> Any:
    """Recursively freeze nested dicts, lists, and sets into read-only structures (B2)."""
    if isinstance(obj, Mapping):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    if isinstance(obj, tuple):
        return tuple(_deep_freeze(v) for v in obj)
    if isinstance(obj, set):
        return frozenset(_deep_freeze(v) for v in obj)
    return obj


@dataclass(frozen=True)
class ImmutableSourceView:
    """Read-only, frozen view of a sanitized SourceAssessment passed to plugins (B2)."""

    url: str
    registrable_domain: str
    source_class: SourceClass
    publication_type: Any
    publication_status: PublicationStatus
    legal_currency: LegalCurrencyStatus
    medical_tier: Any
    retraction_warning: bool
    has_independent_methodology: bool
    publication_date: Optional[str]
    last_updated_date: Optional[str]
    retrieval_timestamp: str
    content_sha256: str
    sanitized_text_sha256: str
    normalized_text: str
    authority_scope_note: str
    owner_scope: str = "public_web"
    sensitivity_tier: str = "public"

    @classmethod
    def from_assessment(cls, sa: SourceAssessment) -> "ImmutableSourceView":
        safe_text = sanitize_text_full(sa.normalized_text)
        return cls(
            url=sa.url,
            registrable_domain=sa.registrable_domain,
            source_class=sa.source_class,
            publication_type=sa.publication_type,
            publication_status=sa.publication_status,
            legal_currency=sa.legal_currency,
            medical_tier=sa.medical_tier,
            retraction_warning=bool(sa.retraction_warning),
            has_independent_methodology=bool(sa.has_independent_methodology),
            publication_date=sa.publication_date,
            last_updated_date=sa.last_updated_date,
            retrieval_timestamp=sa.retrieval_timestamp,
            content_sha256=sa.content_sha256,
            sanitized_text_sha256=sa.sanitized_text_sha256,
            normalized_text=safe_text,
            authority_scope_note=sa.authority_scope_note,
            owner_scope=getattr(sa, "owner_scope", "public_web"),
            sensitivity_tier=getattr(sa, "sensitivity_tier", "public"),
        )


@dataclass(frozen=True)
class ImmutableEdgeView:
    """Read-only frozen view of a ProvenanceEdge (B2)."""

    source_url: str
    target_url: str
    relation: Any
    confidence: DependenceConfidence
    rationale: str

    @classmethod
    def from_edge(cls, edge: ProvenanceEdge) -> "ImmutableEdgeView":
        return cls(
            source_url=edge.source_url,
            target_url=edge.target_url,
            relation=edge.relation,
            confidence=edge.confidence,
            rationale=edge.rationale,
        )


@dataclass(frozen=True)
class ImmutableBaselineReportView:
    """Read-only frozen snapshot of baseline VerificationReport passed to plugins (B2)."""

    verified_facts: Tuple[str, ...] = ()
    corroborated_facts: Tuple[str, ...] = ()
    single_source_claims: Tuple[str, ...] = ()
    conflicting_evidence: Tuple[str, ...] = ()
    unsupported_claims: Tuple[str, ...] = ()
    unverified_citations: Tuple[str, ...] = ()
    followup_queries: Tuple[str, ...] = ()

    @classmethod
    def from_baseline(cls, baseline_report: Any) -> Optional["ImmutableBaselineReportView"]:
        if baseline_report is None:
            return None
        return cls(
            verified_facts=tuple(getattr(baseline_report, "verified_facts", ()) or ()),
            corroborated_facts=tuple(getattr(baseline_report, "corroborated_facts", ()) or ()),
            single_source_claims=tuple(getattr(baseline_report, "single_source_claims", ()) or ()),
            conflicting_evidence=tuple(getattr(baseline_report, "conflicting_evidence", ()) or ()),
            unsupported_claims=tuple(getattr(baseline_report, "unsupported_claims", ()) or ()),
            unverified_citations=tuple(getattr(baseline_report, "unverified_citations", ()) or ()),
            followup_queries=tuple(getattr(baseline_report, "followup_queries", ()) or ()),
        )


def freeze_evidence_snapshot(
    assessments: Sequence[SourceAssessment],
    edges: Sequence[ProvenanceEdge],
    provenance_summary: Dict[str, Any],
    baseline_report: Any = None,
) -> Tuple[
    Tuple[ImmutableSourceView, ...],
    Tuple[ImmutableEdgeView, ...],
    Mapping[str, Any],
    Optional[ImmutableBaselineReportView],
]:
    """Recursively freeze evidence structures so plugins cannot mutate shared state (B2)."""
    frozen_sources = tuple(ImmutableSourceView.from_assessment(a) for a in assessments)
    frozen_edges = tuple(ImmutableEdgeView.from_edge(e) for e in edges)
    frozen_summary = _deep_freeze(dict(provenance_summary or {}))
    frozen_baseline = ImmutableBaselineReportView.from_baseline(baseline_report)
    return frozen_sources, frozen_edges, frozen_summary, frozen_baseline


@dataclass
class ClaimAssessment:
    """Four-axis epistemic claim assessment with originating plugin attribution & explainable rationale."""

    claim_text: str
    domain: str
    claim_assessment: str          # SUPPORTED | CONTESTED | INSUFFICIENT_EVIDENCE
    publication_status: str        # PEER_REVIEWED_VERIFIED | PEER_REVIEW_STATUS_UNVERIFIED | PREPRINT_UNREVIEWED | ...
    legal_currency: str            # CURRENT_VERIFIED | CURRENT_STATUS_NOT_ESTABLISHED | AMENDED_OR_SUPERSEDED | ...
    source_dependence: str         # CONFIRMED_DEPENDENT | LIKELY_DEPENDENT | POSSIBLY_RELATED | INDEPENDENT | UNKNOWN
    evidence_classification: str
    authority_scope_note: str
    plugin_name: str = "unattributed"
    supporting_passages: List[PassageEvidenceRef] = field(default_factory=list)
    conflicting_passages: List[PassageEvidenceRef] = field(default_factory=list)
    rationale: Dict[str, str] = field(default_factory=dict)
    verifier_version: str = REGISTRY_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_text": self.claim_text,
            "domain": self.domain,
            "plugin_name": self.plugin_name,
            "claim_assessment": self.claim_assessment,
            "publication_status": self.publication_status,
            "legal_currency": self.legal_currency,
            "source_dependence": self.source_dependence,
            "evidence_classification": self.evidence_classification,
            "authority_scope_note": self.authority_scope_note,
            "supporting_passages": [copy.deepcopy(p.to_dict()) for p in self.supporting_passages],
            "conflicting_passages": [copy.deepcopy(p.to_dict()) for p in self.conflicting_passages],
            "rationale": copy.deepcopy(dict(self.rationale)),
            "verifier_version": self.verifier_version,
        }


class DomainVerifierPlugin(Protocol):
    name: str
    domain: str

    def supports_category(self, category: str) -> bool:
        ...

    def verify(
        self,
        report_text: str,
        assessments: Sequence[Any],
        edges: Sequence[Any],
        provenance_summary: Mapping[str, Any],
        baseline_report: Any,
    ) -> List[ClaimAssessment]:
        ...


class VerifierRegistry:
    """Deterministic registry of domain verifier plugins with namespace collision protection (A1/A9)."""

    def __init__(self) -> None:
        self._plugins: Dict[str, DomainVerifierPlugin] = {}

    def register(self, plugin: DomainVerifierPlugin, *, replace: bool = False) -> None:
        plugin_name = str(getattr(plugin, "name", "") or "").strip()
        if not plugin_name:
            raise ValueError("Plugin must define a non-empty 'name' attribute.")
        if plugin_name in self._plugins and not replace:
            raise ValueError(f"Duplicate verifier plugin registration rejected: '{plugin_name}'")
        self._plugins[plugin_name] = plugin

    def list_plugins(self) -> List[str]:
        return sorted(self._plugins.keys())

    def get_applicable(self, category: str) -> List[DomainVerifierPlugin]:
        cat = (category or "general").strip().lower()
        matched = [p for p in self._plugins.values() if p.supports_category(cat)]
        return sorted(matched, key=lambda p: (str(p.name), str(getattr(p, "domain", ""))))


class HardwareBaselinePlugin:
    """Wraps accepted DR-4.2 hardware verification outputs into four-axis ClaimAssessment records."""

    name = "hardware_baseline"
    domain = "hardware"

    def supports_category(self, category: str) -> bool:
        return category in {"hardware", "comparison", "technical", "general", ""}

    def verify(
        self,
        report_text: str,
        assessments: Sequence[Any],
        edges: Sequence[Any],
        provenance_summary: Mapping[str, Any],
        baseline_report: Any,
    ) -> List[ClaimAssessment]:
        out: List[ClaimAssessment] = []
        valid_sources = [a for a in assessments if a.source_class != SourceClass.UNVERIFIED_CITATION]
        indep_urls = set(provenance_summary.get("verified_independent_origins", ()))
        unverified_indep_urls = set(provenance_summary.get("unverified_independence_urls", ()))

        def _match_passages(claim_label: str) -> List[PassageEvidenceRef]:
            parts = [p.strip() for p in claim_label.split(":", 1)]
            term = parts[1] if len(parts) == 2 else claim_label
            refs: List[PassageEvidenceRef] = []
            for sa in valid_sources:
                if term.lower() in sa.normalized_text.lower():
                    refs.append(build_passage_evidence_ref(sa, query_or_claim_term=term))
            return refs

        for claim in getattr(baseline_report, "conflicting_evidence", ()):
            passages = _match_passages(claim)
            out.append(ClaimAssessment(
                claim_text=claim,
                domain="hardware",
                plugin_name=self.name,
                claim_assessment="CONTESTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
                source_dependence=DependenceConfidence.UNKNOWN.value,
                evidence_classification="CONTESTED_CONFLICTING",
                authority_scope_note="Conflicting quantitative values detected across sources; unqualified verification suppressed.",
                conflicting_passages=passages,
                rationale={
                    "source_authority": f"{len(passages)} matching source passage(s) evaluated",
                    "independence": "Suppressed due to cross-source value disagreement",
                    "directness": "DIRECT_SPEC_COMPARISON",
                    "contradictions": "Unresolved value conflict across retrieved sources",
                    "evidence_completeness": "Competing values preserved without picking a single winner",
                },
            ))

        for claim in getattr(baseline_report, "verified_facts", ()):
            passages = _match_passages(claim)
            matched_urls = {p.source_url for p in passages}
            matched_indep = matched_urls & indep_urls
            has_primary = any(p.source_class == SourceClass.PRIMARY_INSTITUTIONAL.value for p in passages)

            if len(matched_indep) >= 2 and has_primary:
                dep_conf = DependenceConfidence.INDEPENDENT.value
                ev_class = "PRIMARY_CORROBORATED"
                indep_note = f"{len(matched_indep)} verified independent origins including primary issuer"
            elif has_primary and len(matched_urls) >= 1:
                dep_conf = DependenceConfidence.INDEPENDENT.value if len(matched_indep) >= 1 else DependenceConfidence.UNKNOWN.value
                ev_class = "SINGLE_SOURCE_AUTHORITATIVE" if len(matched_urls) == 1 else "DERIVATIVE_OR_ECHOED"
                indep_note = "Primary institutional spec sheet present; secondary copies not double-counted"
            elif matched_urls and matched_urls.issubset(unverified_indep_urls):
                dep_conf = DependenceConfidence.UNKNOWN.value
                ev_class = "UNVERIFIED_INDEPENDENCE_SINGLE_CLAIM"
                indep_note = "Source independence is UNKNOWN; never promoted to multi-source corroboration"
            else:
                dep_conf = DependenceConfidence.UNKNOWN.value
                ev_class = "SINGLE_SOURCE_SUPPORTED"
                indep_note = f"Supported across {len(matched_urls)} source(s); independence conservatively bounded"

            out.append(ClaimAssessment(
                claim_text=claim,
                domain="hardware",
                plugin_name=self.name,
                claim_assessment="SUPPORTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
                source_dependence=dep_conf,
                evidence_classification=ev_class,
                authority_scope_note="Issuer authoritative for own product specifications; comparative claims require independent measurement.",
                supporting_passages=passages,
                rationale={
                    "source_authority": ", ".join(sorted({p.source_class for p in passages})) or "UNVERIFIED_VENUE",
                    "independence": indep_note,
                    "directness": "DIRECT_ENTITY_BOUND_SPEC",
                    "contradictions": "None detected for this entity and unit",
                    "evidence_completeness": "Entity-anchored specification verified against retrieved source passages",
                },
            ))

        for claim in getattr(baseline_report, "unsupported_claims", ()):
            out.append(ClaimAssessment(
                claim_text=claim,
                domain="hardware",
                plugin_name=self.name,
                claim_assessment="INSUFFICIENT_EVIDENCE",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=LegalCurrencyStatus.NOT_APPLICABLE.value,
                source_dependence=DependenceConfidence.UNKNOWN.value,
                evidence_classification="UNVERIFIED_OR_UNSUPPORTED",
                authority_scope_note="No retrieved passage substantiates this entity-specification pair.",
                rationale={
                    "source_authority": "No substantiating source passage found",
                    "independence": "Not applicable (insufficient evidence)",
                    "directness": "NONE",
                    "contradictions": "None (absence of evidence is not treated as disproof)",
                    "evidence_completeness": "Missing supporting evidence; follow-up query recommended",
                },
            ))

        return out


def _clone_and_sanitize_passage_list(
    passages: Sequence[PassageEvidenceRef],
    url_to_source: Dict[str, ImmutableSourceView],
) -> List[PassageEvidenceRef]:
    """Deep-clone and validate passages returned by plugins so retained references cannot mutate accepted data (B2)."""
    sanitized_out: List[PassageEvidenceRef] = []
    for p in passages:
        if not isinstance(p, PassageEvidenceRef):
            raise TypeError("Plugin returned invalid passage object (expected PassageEvidenceRef).")
        cloned = copy.deepcopy(p)
        orig = url_to_source.get(cloned.source_url)
        if orig is not None:
            if cloned.content_sha256 != orig.content_sha256 or cloned.sanitized_text_sha256 != orig.sanitized_text_sha256:
                raise ValueError(f"Plugin attempted to alter provenance hashes for {cloned.source_url}")
            if cloned.owner_scope != orig.owner_scope or cloned.sensitivity_tier != orig.sensitivity_tier:
                raise ValueError(f"Plugin attempted to alter owner_scope/sensitivity_tier for {cloned.source_url}")
        cloned.excerpt = sanitize_text_full(cloned.excerpt)
        sanitized_out.append(cloned)
    return sanitized_out


def _invoke_plugin_nonblocking(
    plugin: DomainVerifierPlugin,
    *,
    report_text: str,
    frozen_sources: Tuple[ImmutableSourceView, ...],
    frozen_edges: Tuple[ImmutableEdgeView, ...],
    frozen_summary: Mapping[str, Any],
    frozen_baseline: Optional[ImmutableBaselineReportView],
    timeout_seconds: float,
) -> List[ClaimAssessment]:
    """Run plugin with non-blocking shutdown(wait=False) and atomic late-result fencing (B1)."""
    if get_active_orphaned_thread_count() >= MAX_ORPHANED_PLUGIN_THREADS:
        raise RuntimeError(
            f"ORPHANED_THREAD_SATURATION: active orphaned threads >= {MAX_ORPHANED_PLUGIN_THREADS} ({THREAD_TIMEOUT_TECH_DEBT_ID})"
        )

    fenced_lock = threading.Lock()
    timed_out = False

    def _guarded_worker() -> List[ClaimAssessment]:
        try:
            res = plugin.verify(
                report_text,
                frozen_sources,
                frozen_edges,
                frozen_summary,
                frozen_baseline,
            )
            with fenced_lock:
                if timed_out:
                    return []
                return res
        finally:
            with fenced_lock:
                was_orphaned = timed_out
            if was_orphaned:
                _decrement_orphaned_threads()

    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_guarded_worker)
    try:
        raw_results = fut.result(timeout=timeout_seconds)
        pool.shutdown(wait=False, cancel_futures=True)
        return raw_results
    except FuturesTimeoutError:
        with fenced_lock:
            timed_out = True
            _increment_orphaned_threads()
        pool.shutdown(wait=False, cancel_futures=True)
        raise


def run_registry_verification(
    *,
    report_text: str,
    assessments: List[SourceAssessment],
    edges: List[ProvenanceEdge],
    provenance_summary: Dict[str, Any],
    baseline_report: Any,
    category: str,
    registry: VerifierRegistry,
    plugin_timeout_seconds: float = DEFAULT_PLUGIN_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Execute applicable plugins with multi-domain routing, non-blocking timeouts, and explicit conflict preservation."""
    from src.verifiers.router import route_claims_to_plugins

    return route_claims_to_plugins(
        report_text=report_text,
        assessments=assessments,
        edges=edges,
        provenance_summary=provenance_summary,
        baseline_report=baseline_report,
        category=category,
        registry=registry,
        plugin_timeout_seconds=plugin_timeout_seconds,
    )


def build_default_registry() -> VerifierRegistry:
    from src.verifiers.financial import FinancialVerifierPlugin
    from src.verifiers.historical import HistoricalVerifierPlugin
    from src.verifiers.legal import LegalVerifierPlugin
    from src.verifiers.medical import MedicalVerifierPlugin

    reg = VerifierRegistry()
    reg.register(HardwareBaselinePlugin())
    reg.register(FinancialVerifierPlugin())
    reg.register(HistoricalVerifierPlugin())
    reg.register(LegalVerifierPlugin())
    reg.register(MedicalVerifierPlugin())
    return reg
