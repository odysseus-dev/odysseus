"""DR-5.3.5 Multi-Domain Claim Routing & Cross-Plugin Conflict Aggregation.

Enforces DR-5.3.5 invariants:
  1. Preserves canonical claim identity using (claim_index, original_claim_text, normalized_claim_key).
  2. Never merges separate claims merely because their normalized wording matches.
  3. Dispatches overlapping multi-domain claims to every applicable domain plugin.
  4. Preserves HardwareBaselinePlugin's existing baseline_report contract.
  5. Maintains resolution_policy = PRESERVE_CONFLICT_NO_CONSENSUS across plugins.
  6. Isolates plugin exceptions and non-blocking timeouts.
  7. Guarantees deterministic output independent of plugin registration order.
  8. Records missing domains through missing_domain_plugin and missing_domain_domains.
  9. Preserves lazy imports and enable_domain_verifiers=False cold-start isolation.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.research_sources import sanitize_text_full

ROUTER_VERSION = "dr5.3.5-v1.0"
SUPPORTED_ROUTING_DOMAINS: Tuple[str, ...] = (
    "financial",
    "legal",
    "medical",
    "historical",
    "hardware",
)

_DOMAIN_PATTERNS: Dict[str, re.Pattern[str]] = {
    "financial": re.compile(
        r"(?:\$[0-9]+(?:\.[0-9]+)?|\b(?:revenue|eps|net\s+income|ebitda|gaap|non-?gaap|yield|interest\s+rate|inflation|cpi|gdp|fiscal|quarter|q[1-4]\s*20\d{2}|fy\s*20\d{2}|dividend|valuation|guidance|sec\s+filing|10-k|10-q)\b)",
        re.IGNORECASE,
    ),
    "legal": re.compile(
        r"\b(?:statute|u\.s\.c\.|c\.f\.r\.|regulation|regulatory|fda\s+approval|sec\s+rule|act\s+of\s+\d{4}|section\s+\d+|court|ruling|injunction|litigation|lawsuit|compliance|liable|liability|patent|antitrust|gdpr|hipaa|enacted|repealed|amended|superseded)\b",
        re.IGNORECASE,
    ),
    "medical": re.compile(
        r"\b(?:clinical\s+trial|phase\s+[1-4i]+|randomized|placebo|patient|patients|dosage|mg|mcg|efficacy|adverse\s+event|side\s+effect|mortality|survival|biomarker|diagnosis|therapy|therapeutic|treatment|drug|vaccine|contraindicated|fda-approved\s+indication|cochrane|pubmed)\b",
        re.IGNORECASE,
    ),
    "historical": re.compile(
        r"\b(?:\d{1,4}\s*(?:bce|bc|ce|ad)\b|century|dynasty|empire|treaty\s+of|battle\s+of|archival|manuscript|chronicle|ancient|medieval|renaissance|founding|historical|historian|radiocarbon|circa|c\.\s*\d{3,4})\b",
        re.IGNORECASE,
    ),
    "hardware": re.compile(
        r"\b(?:rtx|gtx|rx|ryzen|core\s+i[3579]|core\s+ultra|m[1-5](?:\s+(?:pro|max|ultra))?|epyc|xeon|arc\s+[ab]\d{3,4}|gpu|cpu|npu|vram|gddr\d+|pcie|tdp|cuda|ghz|mhz|nm|gb|tb|wh|mah|fps|benchmark)\b",
        re.IGNORECASE,
    ),
}

_SENTENCE_SPLIT_RE = re.compile(r"(?<!U\.S\.C)(?<!C\.F\.R)(?<!\bc)[.!?]\s+|\n+")


@dataclass(frozen=True)
class CanonicalClaim:
    """Immutable canonical claim identity preserving occurrence index and raw text."""

    claim_index: int
    original_claim_text: str
    normalized_claim_key: str
    matched_domains: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_index": self.claim_index,
            "original_claim_text": self.original_claim_text,
            "normalized_claim_key": self.normalized_claim_key,
            "matched_domains": list(self.matched_domains),
        }


def normalize_claim_key(text: str) -> str:
    """Compute canonical normalized key while leaving raw text intact."""
    cleaned = re.sub(r"\[([^\]]+)\]\(https?://[^)\s]+\)", r"\1", str(text or ""))
    cleaned = cleaned.strip().lstrip("-*?").strip()
    cleaned = re.sub(r"[^a-z0-9\$%./-]+", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def classify_claim_domains(claim_text: str) -> Tuple[str, ...]:
    """Return deterministically ordered matching domains for *claim_text*."""
    text = str(claim_text or "").strip()
    if not text:
        return ()
    matched = [
        dom
        for dom in SUPPORTED_ROUTING_DOMAINS
        if _DOMAIN_PATTERNS[dom].search(text) is not None
    ]
    return tuple(matched)


def extract_canonical_claims(
    report_text: str,
    baseline_report: Any = None,
) -> List[CanonicalClaim]:
    """Extract ordered CanonicalClaim items without merging distinct occurrences."""
    body = str(report_text or "").split("## Evidence Verification Summary")[0]
    claims: List[CanonicalClaim] = []
    idx = 0

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells if c):
                continue
        for seg in _SENTENCE_SPLIT_RE.split(line):
            raw_seg = seg.strip().lstrip("-*?").strip()
            if not raw_seg:
                continue
            norm_key = normalize_claim_key(raw_seg)
            if not norm_key:
                continue
            domains = classify_claim_domains(raw_seg)
            claims.append(
                CanonicalClaim(
                    claim_index=idx,
                    original_claim_text=raw_seg,
                    normalized_claim_key=norm_key,
                    matched_domains=domains,
                )
            )
            idx += 1

    seen_raw = {c.original_claim_text for c in claims}
    if baseline_report is not None:
        for attr in ("conflicting_evidence", "verified_facts", "unsupported_claims"):
            for item in getattr(baseline_report, attr, ()) or ():
                raw_item = str(item or "").strip()
                if not raw_item or raw_item in seen_raw:
                    continue
                seen_raw.add(raw_item)
                norm_key = normalize_claim_key(raw_item)
                domains = classify_claim_domains(raw_item) or ("hardware",)
                claims.append(
                    CanonicalClaim(
                        claim_index=idx,
                        original_claim_text=raw_item,
                        normalized_claim_key=norm_key,
                        matched_domains=domains,
                    )
                )
                idx += 1

    return claims


def _plugin_domains(plugin: Any) -> Set[str]:
    dom = str(getattr(plugin, "domain", "") or "").strip().lower()
    domains: Set[str] = {dom} if dom else set()
    for candidate in SUPPORTED_ROUTING_DOMAINS:
        try:
            if plugin.supports_category(candidate):
                domains.add(candidate)
        except Exception:
            pass
    return domains


def _bind_assessments_to_claims(
    assessments: List[Any],
    claims: Sequence[CanonicalClaim],
) -> Tuple[Dict[int, int], List[Dict[str, Any]]]:
    """Map each assessment object id to its canonical claim_index without mutating rationale keys."""
    by_norm: Dict[str, List[CanonicalClaim]] = {}
    for cl in claims:
        by_norm.setdefault(cl.normalized_claim_key, []).append(cl)

    index_by_obj: Dict[int, int] = {}
    plugin_norm_cursor: Dict[Tuple[str, str], int] = {}

    for ca in assessments:
        norm_key = normalize_claim_key(ca.claim_text)
        candidates = by_norm.get(norm_key, [])
        if not candidates and norm_key:
            candidates = [
                cl
                for cl in claims
                if norm_key in cl.normalized_claim_key or cl.normalized_claim_key in norm_key
            ]

        if candidates:
            cursor_key = (ca.plugin_name, norm_key)
            pos = plugin_norm_cursor.get(cursor_key, 0)
            chosen = candidates[pos % len(candidates)]
            plugin_norm_cursor[cursor_key] = pos + 1
            claim_idx = chosen.claim_index
        else:
            claim_idx = -1

        index_by_obj[id(ca)] = claim_idx

    return index_by_obj, [c.to_dict() for c in claims]


def aggregate_cross_plugin_conflicts(
    assessments: Sequence[Any],
    claims: Sequence[CanonicalClaim],
    index_by_obj: Optional[Mapping[int, int]] = None,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Aggregate conflicts across plugins preserving PRESERVE_CONFLICT_NO_CONSENSUS."""
    by_norm: Dict[str, List[CanonicalClaim]] = {}
    for cl in claims:
        by_norm.setdefault(cl.normalized_claim_key, []).append(cl)

    grouped: Dict[Tuple[int, str], List[Any]] = {}
    plugin_cursor: Dict[Tuple[str, str], int] = {}

    for ca in assessments:
        norm_key = normalize_claim_key(ca.claim_text)
        rat = getattr(ca, "rationale", None) or {}
        if index_by_obj is not None and id(ca) in index_by_obj:
            c_idx = index_by_obj[id(ca)]
        elif "claim_index" in rat:
            try:
                c_idx = int(rat["claim_index"])
            except (TypeError, ValueError):
                c_idx = -1
        else:
            candidates = by_norm.get(norm_key, [])
            if candidates:
                cur = plugin_cursor.get((ca.plugin_name, norm_key), 0)
                c_idx = candidates[cur % len(candidates)].claim_index
                plugin_cursor[(ca.plugin_name, norm_key)] = cur + 1
            else:
                c_idx = -1
        grouped.setdefault((c_idx, norm_key), []).append(ca)

    plugin_conflicts: List[str] = []
    conflict_records: List[Dict[str, Any]] = []

    for c_idx, norm_claim_id in sorted(grouped.keys(), key=lambda k: (k[1], k[0])):
        items = sorted(
            grouped[(c_idx, norm_claim_id)],
            key=lambda c: (c.plugin_name, c.domain, c.claim_assessment, c.evidence_classification),
        )
        states = {c.claim_assessment for c in items}
        domains_involved = sorted({c.domain for c in items})
        if len(states) > 1 or "CONTESTED" in states:
            plugin_conflicts.append(norm_claim_id)
            conflict_records.append(
                {
                    "claim_index": c_idx,
                    "normalized_claim_id": norm_claim_id,
                    "normalized_claim_key": norm_claim_id,
                    "original_claim_texts": sorted({c.claim_text for c in items}),
                    "domains_involved": domains_involved,
                    "is_cross_domain_conflict": len(domains_involved) > 1,
                    "resolution_policy": "PRESERVE_CONFLICT_NO_CONSENSUS",
                    "competing_states": sorted(states),
                    "competing_assessments": [
                        {
                            "plugin_name": c.plugin_name,
                            "domain": c.domain,
                            "claim_text": c.claim_text,
                            "claim_assessment": c.claim_assessment,
                            "evidence_classification": c.evidence_classification,
                            "supporting_passages": [copy.deepcopy(p.to_dict()) for p in c.supporting_passages],
                            "conflicting_passages": [copy.deepcopy(p.to_dict()) for p in c.conflicting_passages],
                        }
                        for c in items
                    ],
                }
            )

    return plugin_conflicts, conflict_records


def route_claims_to_plugins(
    *,
    report_text: str,
    assessments: Sequence[Any],
    edges: Sequence[Any],
    provenance_summary: Dict[str, Any],
    baseline_report: Any,
    category: str,
    registry: Any,
    plugin_timeout_seconds: float,
) -> Dict[str, Any]:
    """Route claims across all applicable domain plugins with deterministic cross-plugin aggregation."""
    from concurrent.futures import TimeoutError as FuturesTimeoutError
    from src.research_sources import DependenceConfidence
    from src.verifiers.base import (
        ClaimAssessment,
        THREAD_TIMEOUT_TECH_DEBT_ID,
        _clone_and_sanitize_passage_list,
        _invoke_plugin_nonblocking,
        freeze_evidence_snapshot,
    )

    frozen_sources, frozen_edges, frozen_summary, frozen_baseline = freeze_evidence_snapshot(
        assessments, edges, provenance_summary, baseline_report
    )
    url_to_source = {s.url: s for s in frozen_sources}
    unverified_indep_urls = set(frozen_summary.get("unverified_independence_urls", ()))

    canonical_claims = extract_canonical_claims(report_text, baseline_report=baseline_report)
    cat_norm = (category or "general").strip().lower()

    requested_domains: Set[str] = set()
    if cat_norm in SUPPORTED_ROUTING_DOMAINS:
        requested_domains.add(cat_norm)
    for cl in canonical_claims:
        requested_domains.update(cl.matched_domains)

    all_plugins = sorted(
        list(getattr(registry, "_plugins", {}).values()),
        key=lambda p: (str(getattr(p, "name", "")), str(getattr(p, "domain", ""))),
    )

    plugin_domain_map: Dict[str, Set[str]] = {
        str(getattr(p, "name", "")): _plugin_domains(p) for p in all_plugins
    }
    covered_domains: Set[str] = set()
    for doms in plugin_domain_map.values():
        covered_domains.update(doms)

    missing_domain_domains = sorted(d for d in requested_domains if d not in covered_domains)

    applicable_by_name: Dict[str, Any] = {}
    for p in registry.get_applicable(cat_norm):
        applicable_by_name[str(p.name)] = p
    for p in all_plugins:
        p_name = str(getattr(p, "name", ""))
        if plugin_domain_map.get(p_name, set()) & requested_domains:
            applicable_by_name[p_name] = p

    applicable = sorted(
        applicable_by_name.values(),
        key=lambda p: (str(getattr(p, "name", "")), str(getattr(p, "domain", ""))),
    )

    missing_domain_plugin = (len(applicable) == 0) or (len(missing_domain_domains) > 0)

    plugin_errors: List[Dict[str, str]] = []
    aggregated: List[ClaimAssessment] = []

    for plugin in applicable:
        p_name = str(getattr(plugin, "name", "unknown_plugin"))
        p_domain = str(getattr(plugin, "domain", "general"))
        try:
            raw_results = _invoke_plugin_nonblocking(
                plugin,
                report_text=report_text,
                frozen_sources=frozen_sources,
                frozen_edges=frozen_edges,
                frozen_summary=frozen_summary,
                frozen_baseline=frozen_baseline,
                timeout_seconds=plugin_timeout_seconds,
            )

            if not isinstance(raw_results, list):
                raise TypeError(f"Plugin '{p_name}' returned non-list output ({type(raw_results).__name__}).")

            for item in raw_results:
                if not isinstance(item, ClaimAssessment):
                    raise TypeError(f"Plugin '{p_name}' returned non-ClaimAssessment item ({type(item).__name__}).")
                if item.claim_assessment not in {"SUPPORTED", "CONTESTED", "INSUFFICIENT_EVIDENCE"}:
                    raise ValueError(f"Plugin '{p_name}' returned invalid claim_assessment '{item.claim_assessment}'.")

                supp = _clone_and_sanitize_passage_list(item.supporting_passages, url_to_source)
                conf = _clone_and_sanitize_passage_list(item.conflicting_passages, url_to_source)

                dep = item.source_dependence
                ev_cls = item.evidence_classification
                if supp and all(p.source_url in unverified_indep_urls for p in supp):
                    dep = DependenceConfidence.UNKNOWN.value
                    if ev_cls == "PRIMARY_CORROBORATED":
                        ev_cls = "UNVERIFIED_INDEPENDENCE_SINGLE_CLAIM"

                safe_rationale = {
                    str(k): sanitize_text_full(str(v))
                    for k, v in copy.deepcopy(dict(item.rationale or {})).items()
                }

                normalized_ca = ClaimAssessment(
                    claim_text=str(item.claim_text),
                    domain=p_domain,
                    plugin_name=p_name,
                    claim_assessment=str(item.claim_assessment),
                    publication_status=str(item.publication_status),
                    legal_currency=str(item.legal_currency),
                    source_dependence=str(dep),
                    evidence_classification=str(ev_cls),
                    authority_scope_note=str(item.authority_scope_note),
                    supporting_passages=supp,
                    conflicting_passages=conf,
                    rationale=safe_rationale,
                    verifier_version=str(item.verifier_version),
                )
                aggregated.append(normalized_ca)

        except FuturesTimeoutError:
            plugin_errors.append(
                {
                    "plugin_name": p_name,
                    "domain": p_domain,
                    "status": "TIMED_OUT_BACKGROUND_THREAD_ORPHANED",
                    "error_type": "TimeoutError",
                    "technical_debt_id": THREAD_TIMEOUT_TECH_DEBT_ID,
                    "detail": f"Plugin execution exceeded {plugin_timeout_seconds:.2f}s timeout; background thread orphaned non-blockingly.",
                }
            )
        except Exception as exc:
            plugin_errors.append(
                {
                    "plugin_name": p_name,
                    "domain": p_domain,
                    "status": "PLUGIN_FAILED_CLOSED",
                    "error_type": type(exc).__name__,
                    "technical_debt_id": THREAD_TIMEOUT_TECH_DEBT_ID,
                    "detail": sanitize_text_full(str(exc))[:200],
                }
            )

    aggregated.sort(
        key=lambda c: (
            0 if (cat_norm in SUPPORTED_ROUTING_DOMAINS and c.domain == cat_norm) else 1,
            c.claim_text.strip().lower(),
            c.plugin_name,
            c.domain,
            c.claim_assessment,
            c.evidence_classification,
        )
    )

    index_by_obj, routed_claims_dicts = _bind_assessments_to_claims(aggregated, canonical_claims)
    plugin_conflicts, conflict_records = aggregate_cross_plugin_conflicts(
        aggregated, canonical_claims, index_by_obj=index_by_obj
    )

    return {
        "claim_assessments": [c.to_dict() for c in aggregated],
        "plugin_errors": plugin_errors,
        "plugin_conflicts": plugin_conflicts,
        "conflict_records": conflict_records,
        "missing_domain_plugin": missing_domain_plugin,
        "missing_domain_domains": missing_domain_domains,
        "routed_claims": routed_claims_dicts,
    }
