"""DR-3 / DR-4.2 Deterministic Claim, Specification, Conflict & Citation Verifier.

Cross-checks synthesized research reports against retrieved findings and URLs:
  1. Citation verification: ensures inline [label](url) links match fetched URLs.
  2. Entity-anchored specification binding: associates each quantitative spec
     exclusively with its owning hardware entity (via Markdown table column headers
     or clause-bounded nearest-entity proximity) so different product models never
     inherit each other's specifications.
  3. Conflict suppression: identifies disagreements across independent sources for
     the same entity + unit and prevents conflicting values from receiving
     unqualified verified status.
  4. Corroboration tiering: distinguishes multi-source corroborated facts
     (>= 2 independent sources) from single-source supported claims (1 source)
     and unsupported claims (0 sources).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# Matches explicit hardware product/model identifiers without matching generic words like "architecture".
_ENTITY_RE = re.compile(
    r"\b(?:RTX|GTX|RX|Ryzen|Core\s+i[3579]|Core\s+Ultra|M[1-5](?:\s+(?:Pro|Max|Ultra))?|EPYC|Xeon)\s*[A-Za-z0-9-]+"
    r"(?:\s+(?:Ti|SUPER|XTX|XT|X3D|Pro|Max|Ultra))?\b"
    r"|\bArc\s+[AB]\d{3,4}[A-Za-z0-9]*\b",
    re.IGNORECASE,
)

# Matches quantitative technical specs while excluding rate units like GB/s, MB/s, TB/s, or bps.
_SPEC_RE = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:GB|TB|MB|W|Wh|mAh|GHz|MHz|nm|CUDA(?:\s+cores)?|cores|threads|fps|ms)(?!\s*(?:/\s*s|bps)\b)\b)",
    re.IGNORECASE,
)

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+|\s*;\s*|\b(?:whereas|while|compared\s+to|versus|vs\.?)\b", re.IGNORECASE)


@dataclass
class VerificationReport:
    verified_facts: List[str] = field(default_factory=list)
    corroborated_facts: List[str] = field(default_factory=list)
    single_source_claims: List[str] = field(default_factory=list)
    conflicting_evidence: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    unverified_citations: List[str] = field(default_factory=list)
    followup_queries: List[str] = field(default_factory=list)
    claim_assessments: Optional[List[Dict[str, Any]]] = None
    provenance_summary: Optional[Dict[str, Any]] = None
    provenance_edges: Optional[List[Dict[str, str]]] = None
    plugin_errors: Optional[List[Dict[str, str]]] = None
    plugin_conflicts: Optional[List[str]] = None
    conflict_records: Optional[List[Dict[str, Any]]] = None
    missing_domain_plugin: Optional[bool] = None
    missing_domain_domains: Optional[List[str]] = None
    routed_claims: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "verified_facts": list(self.verified_facts),
            "corroborated_facts": list(self.corroborated_facts),
            "single_source_claims": list(self.single_source_claims),
            "conflicting_evidence": list(self.conflicting_evidence),
            "unsupported_claims": list(self.unsupported_claims),
            "unverified_citations": list(self.unverified_citations),
            "followup_queries": list(self.followup_queries),
        }
        if self.claim_assessments is not None:
            out["claim_assessments"] = list(self.claim_assessments)
        if self.provenance_summary is not None:
            out["provenance_summary"] = dict(self.provenance_summary)
        if self.provenance_edges is not None:
            out["provenance_edges"] = list(self.provenance_edges)
        if self.plugin_errors is not None:
            out["plugin_errors"] = list(self.plugin_errors)
        if self.plugin_conflicts is not None:
            out["plugin_conflicts"] = list(self.plugin_conflicts)
        if self.conflict_records is not None:
            out["conflict_records"] = list(self.conflict_records)
        if self.missing_domain_plugin is not None:
            out["missing_domain_plugin"] = bool(self.missing_domain_plugin)
        if self.missing_domain_domains is not None:
            out["missing_domain_domains"] = list(self.missing_domain_domains)
        if self.routed_claims is not None:
            out["routed_claims"] = list(self.routed_claims)
        return out

    def has_gaps(self) -> bool:
        return bool(self.unsupported_claims or self.unverified_citations or self.conflicting_evidence)
    def to_markdown(self) -> str:
        lines = ["## Evidence Verification Summary"]
        lines.append(f"- **Verified Claims (Non-Conflicting):** {len(self.verified_facts)} (Corroborated 2+ sources: {len(self.corroborated_facts)}, Single-source: {len(self.single_source_claims)})")
        for item in self.corroborated_facts[:8]:
            lines.append(f"  - [Verified] {item} (corroborated: 2+ sources)")
        for item in self.single_source_claims[:8]:
            lines.append(f"  - [Verified] {item} (1 source)")
        lines.append(f"- **Conflicting Evidence:** {len(self.conflicting_evidence)}")
        for item in self.conflicting_evidence[:6]:
            lines.append(f"  - [Conflict] {item}")
        lines.append(f"- **Unsupported / Unverified Claims:** {len(self.unsupported_claims) + len(self.unverified_citations)}")
        for item in self.unsupported_claims[:8]:
            lines.append(f"  - [Unverified] {item}")
        for url in self.unverified_citations[:5]:
            lines.append(f"  - [Unverified Citation] {url}")
        return "\n".join(lines)


def _norm_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    parsed = urlparse(u)
    if not parsed.scheme or not parsed.netloc:
        return u.lower()
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}".rstrip("/")


def _norm_entity(ent: str) -> str:
    return re.sub(r"\s+", " ", ent.strip()).upper()


def _norm_spec(spec: str) -> str:
    return re.sub(r"\s+", "", spec.lower()).replace(",", "")


def _spec_unit(spec: str) -> str:
    s = spec.strip().lower()
    if s.startswith("$"):
        return "usd"
    m = re.search(r"(gb|tb|mb|wh|mah|ghz|mhz|nm|cuda|cores|threads|fps|ms|w)\b", s)
    return m.group(1) if m else "value"


def _strip_urls(text: str) -> str:
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", text)
    return re.sub(r"https?://\S+", "", cleaned)


def _extract_clause_entity_specs(clause: str) -> List[Tuple[str, str]]:
    """Bind each quantitative spec in a clause to its nearest owning entity."""
    clean = _strip_urls(clause).strip()
    if not clean:
        return []
    ent_matches = list(_ENTITY_RE.finditer(clean))
    spec_matches = list(_SPEC_RE.finditer(clean))
    if not ent_matches or not spec_matches:
        return []

    pairs: List[Tuple[str, str]] = []
    for sm in spec_matches:
        s_start = sm.start()
        # Prefer the closest entity that appears at or before this spec in the clause.
        preceding = [em for em in ent_matches if em.start() <= s_start]
        if preceding:
            owner = preceding[-1].group(0).strip()
        else:
            # Spec precedes the first entity in the clause (e.g., "With 24 GB VRAM, the RTX 4090...")
            owner = ent_matches[0].group(0).strip()
        pairs.append((owner, sm.group(0).strip()))
    return pairs


def _extract_text_entity_specs(text: str) -> List[Tuple[str, str]]:
    """Extract (entity, spec) pairs across prose clauses and Markdown tables without cross-column leakage."""
    pairs: List[Tuple[str, str]] = []
    table_col_entities: Dict[int, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            table_col_entities = {}
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells if c):
                continue
            # Check if this row is a header row defining entity columns
            header_map: Dict[int, str] = {}
            for idx, cell in enumerate(cells):
                ents = list(_ENTITY_RE.finditer(_strip_urls(cell)))
                if len(ents) == 1 and not list(_SPEC_RE.finditer(_strip_urls(cell))):
                    header_map[idx] = ents[0].group(0).strip()
            if header_map:
                table_col_entities = header_map
                continue
            if table_col_entities:
                for idx, cell in enumerate(cells):
                    if idx in table_col_entities:
                        owner = table_col_entities[idx]
                        for sm in _SPEC_RE.finditer(_strip_urls(cell)):
                            pairs.append((owner, sm.group(0).strip()))
                continue

        table_col_entities = {}
        if line.startswith("#"):
            continue
        for clause in _CLAUSE_SPLIT_RE.split(line):
            pairs.extend(_extract_clause_entity_specs(clause))

    return pairs


def _finding_texts(findings: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        url = str(f.get("url") or f.get("source_url") or "").strip()
        title = str(f.get("title") or "").strip()
        body = " ".join(
            str(f.get(k) or "")
            for k in ("summary", "content", "snippet", "text", "excerpt")
        ).strip()
        if title or body or url:
            out.append({"url": url, "title": title, "text": f"{title}\n{body}".strip()})
    return out


def verify_report_evidence(
    report: str,
    findings: List[Dict[str, Any]],
    analyzed_urls: Optional[Set[str]] = None,
    *,
    enable_domain_verifiers: bool = False,
    category: str = "general",
    registry: Optional[Any] = None,
) -> VerificationReport:
    """Cross-check report claims, model specs, and citations against retrieved findings."""
    vr = VerificationReport()
    text = str(report or "")
    if not text.strip():
        return vr

    parsed_findings = _finding_texts(findings)
    known_urls: Set[str] = {_norm_url(f["url"]) for f in parsed_findings if f["url"]}
    for u in analyzed_urls or []:
        if u:
            known_urls.add(_norm_url(str(u)))

    # 1. Citation Verification: every inline markdown URL must match a retrieved/analyzed source
    for label, raw_url in _MARKDOWN_LINK_RE.findall(text):
        norm = _norm_url(raw_url)
        if known_urls and norm not in known_urls:
            entry = f"{label.strip()} ({raw_url.strip()})"
            if entry not in vr.unverified_citations:
                vr.unverified_citations.append(entry)

    # 2. Source-Level Entity-Spec Extraction & Conflict Detection
    # Maps (ENT_UPPER, unit) -> {norm_spec -> set of distinct source IDs}
    source_entity_specs: Dict[Tuple[str, str], Dict[str, Set[str]]] = {}
    for idx, pf in enumerate(parsed_findings):
        src_id = _norm_url(pf["url"]) if pf["url"] else f"source_{idx}"
        for ent, sp in _extract_text_entity_specs(pf["text"]):
            ent_u = _norm_entity(ent)
            sp_n = _norm_spec(sp)
            unit = _spec_unit(sp_n)
            bucket = source_entity_specs.setdefault((ent_u, unit), {})
            bucket.setdefault(sp_n, set()).add(src_id)

    conflicted_entity_units: Set[Tuple[str, str]] = set()
    for (ent_u, unit), val_map in sorted(source_entity_specs.items()):
        if len(val_map) > 1:
            conflicted_entity_units.add((ent_u, unit))
            variants = ", ".join(f"{val} ({len(srcs)} src)" for val, srcs in sorted(val_map.items()))
            msg = f"{ent_u} has conflicting {unit.upper()} values across sources: {variants}"
            if msg not in vr.conflicting_evidence:
                vr.conflicting_evidence.append(msg)

    # 3. Report Claim Verification with Conflict Suppression & Corroboration Tiering
    unverified_entities: Set[str] = set()
    body_only = text.split("## Evidence Verification Summary")[0]
    seen_claims: Set[str] = set()

    for ent, sp in _extract_text_entity_specs(body_only):
        ent_u = _norm_entity(ent)
        sp_n = _norm_spec(sp)
        unit = _spec_unit(sp_n)
        claim_label = f"{ent}: {sp}"
        if claim_label in seen_claims:
            continue
        seen_claims.add(claim_label)

        # Conflicting values for this (entity, unit) must NEVER receive unqualified verified status
        if (ent_u, unit) in conflicted_entity_units:
            unverified_entities.add(ent)
            continue

        supporting_srcs = source_entity_specs.get((ent_u, unit), {}).get(sp_n, set())
        if len(supporting_srcs) >= 2:
            if claim_label not in vr.verified_facts:
                vr.verified_facts.append(claim_label)
            if claim_label not in vr.corroborated_facts:
                vr.corroborated_facts.append(claim_label)
        elif len(supporting_srcs) == 1:
            if claim_label not in vr.verified_facts:
                vr.verified_facts.append(claim_label)
            if claim_label not in vr.single_source_claims:
                vr.single_source_claims.append(claim_label)
        else:
            if claim_label not in vr.unsupported_claims:
                vr.unsupported_claims.append(claim_label)
            unverified_entities.add(ent)

    for ent in sorted(unverified_entities)[:3]:
        vr.followup_queries.append(f"{ent} official specifications")

    if not enable_domain_verifiers:
        return vr

    # Lazy import only when enable_domain_verifiers=True (preserves DR-4.2 cold-start isolation)
    from src.research_sources import assess_source_authority, build_provenance_graph
    from src.verifiers.base import build_default_registry, run_registry_verification

    assessments = [assess_source_authority(f, is_verified_fetched_url=True) for f in (findings or [])]
    edges, prov_summary = build_provenance_graph(assessments)
    active_registry = registry if registry is not None else build_default_registry()
    reg_out = run_registry_verification(
        report_text=report,
        assessments=assessments,
        edges=edges,
        provenance_summary=prov_summary,
        baseline_report=vr,
        category=category,
        registry=active_registry,
    )

    vr.claim_assessments = reg_out["claim_assessments"]
    vr.provenance_summary = prov_summary
    vr.provenance_edges = [e.to_dict() for e in edges]
    vr.plugin_errors = reg_out["plugin_errors"]
    vr.plugin_conflicts = reg_out["plugin_conflicts"]
    vr.conflict_records = reg_out["conflict_records"]
    vr.missing_domain_plugin = reg_out["missing_domain_plugin"]
    vr.missing_domain_domains = list(reg_out.get("missing_domain_domains", ()))
    vr.routed_claims = list(reg_out.get("routed_claims", ()))
    return vr
