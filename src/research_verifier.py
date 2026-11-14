"""DR-3 Deterministic Claim, Specification, Conflict & Citation Verifier.

Cross-checks synthesized research reports against retrieved findings and URLs:
  1. Citation verification: ensures inline [label](url) links match fetched URLs.
  2. Specification verification: checks entity + quantitative spec pairs against
     source passages mentioning that specific entity (prevents cross-model conflation).
  3. Conflict detection: identifies disagreements across independent sources for the
     same entity and measurement unit.
  4. Evidence classification: separates Verified Facts, Conflicting Evidence, and
     Unsupported / Unverified Claims, and proposes targeted follow-up queries.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import urlparse

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
# Matches model/product identifiers like RTX 4090, RTX 5090, RX 7900 XTX, Ryzen 9 9950X, M4 Max, etc.
_ENTITY_RE = re.compile(
    r"\b(?:RTX|GTX|RX|Arc|Ryzen|Core\s+i[3579]|Core\s+Ultra|M[1-5](?:\s+(?:Pro|Max|Ultra))?|EPYC|Xeon)\s*[A-Za-z0-9\-]+"
    r"(?:\s+(?:Ti|SUPER|XTX|XT|X3D|Pro|Max|Ultra))?\b",
    re.IGNORECASE,
)
# Matches quantitative technical specs (e.g., 24 GB, 32GB, 575W, 450 W, $1,999, 2.52 GHz, 16384 CUDA)
_SPEC_RE = re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:GB|TB|MB|W|Wh|mAh|GHz|MHz|nm|CUDA(?:\s+cores)?|cores|threads|fps|ms)\b)",
    re.IGNORECASE,
)


@dataclass
class VerificationReport:
    verified_facts: List[str] = field(default_factory=list)
    conflicting_evidence: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    unverified_citations: List[str] = field(default_factory=list)
    followup_queries: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def has_gaps(self) -> bool:
        return bool(self.unsupported_claims or self.unverified_citations or self.conflicting_evidence)

    def to_markdown(self) -> str:
        lines = ["## Evidence Verification Summary"]
        lines.append(f"- **Verified Facts:** {len(self.verified_facts)}")
        for item in self.verified_facts[:8]:
            lines.append(f"  - [Verified] {item}")
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


def _norm_spec(spec: str) -> str:
    return re.sub(r"\s+", "", spec.lower()).replace(",", "")


def _spec_unit(spec: str) -> str:
    s = spec.strip().lower()
    if s.startswith("$"):
        return "usd"
    m = re.search(r"(gb|tb|mb|wh|mah|ghz|mhz|nm|cuda|cores|threads|fps|ms|w)\b", s)
    return m.group(1) if m else "value"


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
    findings: Iterable[Dict[str, Any]],
    *,
    analyzed_urls: Optional[Iterable[str]] = None,
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

    # 2. Conflict Detection across independent sources for the same entity + unit
    source_entity_specs: Dict[tuple[str, str], Dict[str, Set[str]]] = {}
    for idx, pf in enumerate(parsed_findings):
        src_id = pf["url"] or f"source_{idx}"
        for line in re.split(r"(?<=[.!?])\s+|\n+", pf["text"]):
            ents = {m.group(0).strip().upper() for m in _ENTITY_RE.finditer(line)}
            specs = [_norm_spec(m.group(0)) for m in _SPEC_RE.finditer(line)]
            if not ents or not specs:
                continue
            for ent in ents:
                for sp in specs:
                    unit = _spec_unit(sp)
                    bucket = source_entity_specs.setdefault((ent, unit), {})
                    bucket.setdefault(sp, set()).add(src_id)

    for (ent, unit), val_map in sorted(source_entity_specs.items()):
        if len(val_map) > 1:
            variants = ", ".join(f"{val} ({len(srcs)} src)" for val, srcs in sorted(val_map.items()))
            msg = f"{ent} has conflicting {unit.upper()} values across sources: {variants}"
            if msg not in vr.conflicting_evidence:
                vr.conflicting_evidence.append(msg)

    # 3. Specification & Claim Verification per report statement/row
    unverified_entities: Set[str] = set()
    body_only = text.split("## Evidence Verification Summary")[0]
    for raw_line in body_only.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("| ---"):
            continue
        ents = list(dict.fromkeys(m.group(0).strip() for m in _ENTITY_RE.finditer(line)))
        specs = list(dict.fromkeys(m.group(0).strip() for m in _SPEC_RE.finditer(line)))
        if not ents or not specs:
            continue

        for ent in ents:
            ent_upper = ent.upper()
            matching_sources = [pf for pf in parsed_findings if ent_upper in pf["text"].upper()]
            for sp in specs:
                sp_norm = _norm_spec(sp)
                claim_label = f"{ent}: {sp}"
                supported = any(
                    sp_norm in _norm_spec(pf["text"])
                    for pf in matching_sources
                )
                if supported:
                    if claim_label not in vr.verified_facts:
                        vr.verified_facts.append(claim_label)
                else:
                    if claim_label not in vr.unsupported_claims:
                        vr.unsupported_claims.append(claim_label)
                    unverified_entities.add(ent)

    for ent in sorted(unverified_entities)[:3]:
        vr.followup_queries.append(f"{ent} official specifications")

    return vr
