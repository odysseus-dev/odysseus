"""DR-5.8 Historical Domain Verifier Plugin.

Enforces evidence rules DR5.8-H1 through DR5.8-H5 behind enable_domain_verifiers=False:
  - H1: Temporal & era anchor discipline (BCE/BC vs. CE/AD, century, and circa/approximate
        bounds; prevents cross-era conflation and exact-date claims on approximate sources).
  - H2: Primary archival / contemporary chronicle vs. tertiary / unverified blog tiering
        (unverified blogs never auto-corroborate historical claims).
  - H3: Historiographical debate & contradictory chronology preservation (disputed dates,
        authorship, or revisionist debates remain CONTESTED with supporting & conflicting
        passages rather than picking a recency winner).
  - H4: Myth / legend / apocryphal attribution hygiene (claims framed as myth, legend,
        folklore, or apocryphal tradition fail closed).
  - H5: Cold-start & disabled-flag isolation (lazy import inside build_default_registry()).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PassageEvidenceRef,
    PublicationStatus,
    SourceClass,
    build_passage_evidence_ref,
)
from src.verifiers.base import ClaimAssessment

HISTORICAL_VERIFIER_VERSION = "dr5.8-v1.0"

_ERA_YEAR_RE = re.compile(r"\b(\d{1,4})\s*(BCE|BC|CE|AD)\b", re.IGNORECASE)
_CENTURY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+century(?:\s*(BCE|BC|CE|AD))?\b", re.IGNORECASE)
_PLAIN_YEAR_RE = re.compile(r"\b(1\d{3}|20\d{2})\b")
_CIRCA_RE = re.compile(
    r"\b(?:circa|c\.\s*\d|approximately|around\s+\d|traditionally\s+dated|uncertain\s+date|disputed\s+date)\b",
    re.IGNORECASE,
)
_HISTORICAL_TOPIC_RE = re.compile(
    r"\b(?:\d{1,4}\s*(?:bce|bc|ce|ad)\b|century|dynasty|empire|treaty|battle|archival|archive|manuscript|chronicle|ancient|medieval|renaissance|founding|historical|historian|radiocarbon|circa|declaration|constitution|revolution|republic|kingdom|emperor|pharaoh)\b",
    re.IGNORECASE,
)
_DEBATE_RE = re.compile(
    r"\b(?:historians\s+debate|scholarly\s+debate|disputed|contested|revisionist|contradictory|disagree|alternative\s+date|no\s+consensus)\b",
    re.IGNORECASE,
)
_MYTH_RE = re.compile(
    r"\b(?:legend|myth|mythological|folklore|apocryphal|fictional|unverified\s+tradition|semi-legendary|fabricated)\b",
    re.IGNORECASE,
)
_PRIMARY_ARCHIVE_DOMAIN_RE = re.compile(
    r"(?:\.gov|\.edu|\.ac\.uk|archives\.gov|loc\.gov|britishmuseum\.org|bl\.uk|bnf\.fr|si\.edu|archive\.org|jstor\.org|cambridge\.org|oxfordacademic|oup\.com)",
    re.IGNORECASE,
)
_PRIMARY_KEYWORD_RE = re.compile(
    r"\b(?:primary\s+source|archival|manuscript|charter|treaty\s+text|chronicle|inscription|epigraphy|radiocarbon|excavation|peer-reviewed)\b",
    re.IGNORECASE,
)
_TERTIARY_BLOG_RE = re.compile(
    r"(?:blog|wordpress|medium\.com|substack|rumor|trivia|listicle|seo)",
    re.IGNORECASE,
)

_STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "from", "this", "were", "was", "are",
    "into", "upon", "over", "under", "during", "after", "before", "between",
    "about", "which", "their", "there", "these", "those", "have", "has", "had",
    "in", "on", "at", "by", "of", "to", "as", "is", "it", "an", "or",
})


@dataclass(frozen=True)
class _TemporalAnchor:
    year: Optional[int]
    era: str
    century: Optional[int]
    is_approximate: bool


@dataclass(frozen=True)
class _ParsedHistoricalClaim:
    raw_text: str
    anchor: Optional[_TemporalAnchor]
    keywords: Tuple[str, ...]
    claims_myth_framed: bool


def _norm_era(raw_era: Optional[str], default_ce: bool = False) -> str:
    if not raw_era:
        return "CE" if default_ce else "UNKNOWN"
    u = raw_era.strip().upper()
    if u in {"BCE", "BC"}:
        return "BCE"
    if u in {"CE", "AD"}:
        return "CE"
    return "UNKNOWN"


def _extract_temporal_anchor(text: str) -> Optional[_TemporalAnchor]:
    approx = bool(_CIRCA_RE.search(text or ""))
    era_m = _ERA_YEAR_RE.search(text or "")
    if era_m:
        yr = int(era_m.group(1))
        era = _norm_era(era_m.group(2))
        cent = ((yr - 1) // 100) + 1 if yr > 0 else None
        return _TemporalAnchor(year=yr, era=era, century=cent, is_approximate=approx)

    cent_m = _CENTURY_RE.search(text or "")
    if cent_m:
        cent = int(cent_m.group(1))
        era = _norm_era(cent_m.group(2), default_ce=True)
        return _TemporalAnchor(year=None, era=era, century=cent, is_approximate=approx)

    yr_m = _PLAIN_YEAR_RE.search(text or "")
    if yr_m:
        yr = int(yr_m.group(1))
        cent = ((yr - 1) // 100) + 1
        return _TemporalAnchor(year=yr, era="CE", century=cent, is_approximate=approx)

    return None


def _extract_keywords(text: str) -> Tuple[str, ...]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", (text or "").lower())
    out: List[str] = []
    seen: Set[str] = set()
    for tok in tokens:
        if tok in _STOPWORDS or tok in {"bce", "bc", "ce", "ad", "century", "circa"}:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return tuple(out)


def _extract_historical_claims(report_text: str) -> List[_ParsedHistoricalClaim]:
    claims: List[_ParsedHistoricalClaim] = []
    for raw_line in (report_text or "").splitlines():
        line = raw_line.strip().lstrip("-*?").strip()
        if not line or line.startswith("#"):
            continue
        anchor = _extract_temporal_anchor(line)
        if anchor is None and not _HISTORICAL_TOPIC_RE.search(line):
            continue
        kws = _extract_keywords(line)
        if not kws:
            continue
        claims.append(
            _ParsedHistoricalClaim(
                raw_text=line,
                anchor=anchor,
                keywords=kws,
                claims_myth_framed=bool(_MYTH_RE.search(line)),
            )
        )
    return claims


def _sentence_windows(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?:(?<!\d)\.(?!\d)|[!?])\s+|\n+", text or "") if s.strip()]


def _is_primary_or_scholarly_source(sa: Any) -> bool:
    url = str(getattr(sa, "url", "") or "")
    title = str(getattr(sa, "title", "") or "")
    txt = str(getattr(sa, "normalized_text", "") or "")
    if _TERTIARY_BLOG_RE.search(url) or _TERTIARY_BLOG_RE.search(title):
        return False
    if _PRIMARY_ARCHIVE_DOMAIN_RE.search(url):
        return True
    if getattr(sa, "is_peer_reviewed", False):
        return True
    if _PRIMARY_KEYWORD_RE.search(title) or _PRIMARY_KEYWORD_RE.search(txt):
        return True
    return False


def _anchors_compatible(claim_a: Optional[_TemporalAnchor], src_a: Optional[_TemporalAnchor]) -> Tuple[bool, str]:
    if claim_a is None or src_a is None:
        return True, ""
    if claim_a.era != "UNKNOWN" and src_a.era != "UNKNOWN" and claim_a.era != src_a.era:
        return False, f"era_mismatch ({claim_a.era} vs {src_a.era})"
    if claim_a.year is not None and src_a.year is not None and claim_a.year != src_a.year:
        return False, f"year_mismatch ({claim_a.year} vs {src_a.year})"
    if claim_a.century is not None and src_a.century is not None and claim_a.century != src_a.century:
        return False, f"century_mismatch ({claim_a.century} vs {src_a.century})"
    return True, ""


class HistoricalVerifierPlugin:
    """Domain verifier for historical chronology, primary archival sources, and historiographical consensus."""

    name = "historical_verifier"
    domain = "historical"

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() in {
            "historical",
            "history",
            "archival",
            "archaeology",
            "antiquity",
        }

    def verify(
        self,
        report_text: str,
        assessments: Sequence[Any],
        edges: Sequence[Any],
        provenance_summary: Mapping[str, Any],
        baseline_report: Any,
    ) -> List[ClaimAssessment]:
        valid_sources = [a for a in assessments if a.source_class != SourceClass.UNVERIFIED_CITATION]
        indep_urls = set(provenance_summary.get("verified_independent_origins", ()))
        unverified_indep_urls = set(provenance_summary.get("unverified_independence_urls", ()))

        out: List[ClaimAssessment] = []
        for claim in _extract_historical_claims(report_text):
            supporting_passages: List[PassageEvidenceRef] = []
            conflicting_passages: List[PassageEvidenceRef] = []
            matched_primary_urls: Set[str] = set()
            matched_tertiary_urls: Set[str] = set()
            matched_all_urls: Set[str] = set()
            approx_conflict = False
            myth_conflict = False
            debate_detected = False
            era_or_date_conflict_reasons: List[str] = []

            min_kw_hits = 1 if len(claim.keywords) == 1 else 2

            for sa in valid_sources:
                src_text = str(getattr(sa, "normalized_text", "") or "")
                is_primary = _is_primary_or_scholarly_source(sa)
                sa_url = str(getattr(sa, "url", "") or "")

                for sent in _sentence_windows(src_text):
                    sent_l = sent.lower()
                    kw_hits = sum(1 for kw in claim.keywords if kw in sent_l)
                    if kw_hits < min_kw_hits:
                        continue

                    src_anchor = _extract_temporal_anchor(sent)
                    compat, mismatch_reason = _anchors_compatible(claim.anchor, src_anchor)
                    term_hint = claim.keywords[0]
                    ref = build_passage_evidence_ref(sa, query_or_claim_term=term_hint)

                    if not compat:
                        era_or_date_conflict_reasons.append(mismatch_reason)
                        if ref not in conflicting_passages:
                            conflicting_passages.append(ref)
                        continue

                    if _DEBATE_RE.search(sent):
                        debate_detected = True
                        if ref not in conflicting_passages:
                            conflicting_passages.append(ref)
                        continue

                    if _MYTH_RE.search(sent) and not claim.claims_myth_framed:
                        myth_conflict = True
                        if ref not in conflicting_passages:
                            conflicting_passages.append(ref)
                        continue

                    if (
                        claim.anchor is not None
                        and not claim.anchor.is_approximate
                        and src_anchor is not None
                        and src_anchor.is_approximate
                    ):
                        approx_conflict = True
                        if ref not in conflicting_passages:
                            conflicting_passages.append(ref)
                        continue

                    if ref not in supporting_passages:
                        supporting_passages.append(ref)
                    if sa_url:
                        matched_all_urls.add(sa_url)
                        if is_primary:
                            matched_primary_urls.add(sa_url)
                        else:
                            matched_tertiary_urls.add(sa_url)

            if matched_all_urls and matched_all_urls.issubset(indep_urls) and len(matched_all_urls) >= 2:
                src_dep = DependenceConfidence.INDEPENDENT.value
            elif matched_all_urls & unverified_indep_urls:
                src_dep = DependenceConfidence.UNKNOWN.value
            elif len(matched_all_urls) == 1:
                src_dep = DependenceConfidence.INDEPENDENT.value
            else:
                src_dep = DependenceConfidence.UNKNOWN.value

            has_peer_reviewed = any(
                getattr(sa, "is_peer_reviewed", False) and str(getattr(sa, "url", "") or "") in matched_primary_urls
                for sa in valid_sources
            )
            pub_status = (
                PublicationStatus.PEER_REVIEWED_VERIFIED.value
                if has_peer_reviewed
                else PublicationStatus.NOT_APPLICABLE.value
            )

            has_established_anchor = bool(
                claim.anchor is not None
                and claim.anchor.era != "UNKNOWN"
                and (claim.anchor.year is not None or claim.anchor.century is not None)
                and not claim.anchor.is_approximate
            )
            currency_status = (
                LegalCurrencyStatus.CURRENT_VERIFIED.value
                if has_established_anchor and matched_primary_urls and not approx_conflict and not era_or_date_conflict_reasons
                else LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
            )

            if conflicting_passages and supporting_passages:
                verdict = "CONTESTED"
                ev_cls = "CONTESTED_CHRONOLOGY_OR_HISTORIOGRAPHY"
                note = "Retrieved sources contain conflicting dates/eras, approximate bounds, or historiographical debate."
            elif era_or_date_conflict_reasons:
                verdict = "CONTESTED"
                ev_cls = "ERA_OR_CHRONOLOGY_MISMATCH"
                note = f"Chronological mismatch detected ({', '.join(sorted(set(era_or_date_conflict_reasons)))})."
            elif myth_conflict:
                verdict = "INSUFFICIENT_EVIDENCE"
                ev_cls = "MYTH_OR_APOCRYPHAL_FRAMING"
                note = "Retrieved sources frame this event as legend, myth, or apocryphal tradition rather than verified history."
            elif approx_conflict:
                verdict = "CONTESTED"
                ev_cls = "APPROXIMATE_CHRONOLOGY_MISMATCH"
                note = "Claim states a precise date/period, whereas retrieved sources qualify the chronology as approximate (circa)."
            elif debate_detected:
                verdict = "CONTESTED"
                ev_cls = "HISTORIOGRAPHICAL_DEBATE"
                note = "Retrieved sources indicate active scholarly debate or lack of historical consensus."
            elif not supporting_passages:
                verdict = "INSUFFICIENT_EVIDENCE"
                ev_cls = "NO_PRIMARY_HISTORICAL_EVIDENCE"
                note = "No retrieved source substantiates the historical event and chronological anchor."
            elif not matched_primary_urls and matched_tertiary_urls:
                verdict = "INSUFFICIENT_EVIDENCE"
                ev_cls = "UNVERIFIED_TERTIARY_SOURCE"
                note = "Only unverified tertiary/blog sources matched; primary archival or scholarly corroboration required."
            else:
                verdict = "SUPPORTED"
                ev_cls = (
                    "CORROBORATED_PRIMARY_HISTORICAL"
                    if len(matched_primary_urls) >= 2 and src_dep == DependenceConfidence.INDEPENDENT.value
                    else "SINGLE_SOURCE_AUTHORITATIVE"
                )
                note = "Substantiated by primary archival or scholarly historical evidence with consistent chronological anchor."

            out.append(
                ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    claim_assessment=verdict,
                    publication_status=pub_status,
                    legal_currency=currency_status,
                    source_dependence=src_dep,
                    evidence_classification=ev_cls,
                    authority_scope_note=note,
                    plugin_name=self.name,
                    supporting_passages=supporting_passages,
                    conflicting_passages=conflicting_passages,
                    rationale={
                        "rule_version": HISTORICAL_VERIFIER_VERSION,
                        "primary_sources_matched": str(len(matched_primary_urls)),
                        "tertiary_sources_matched": str(len(matched_tertiary_urls)),
                        "chronology_anchor": (
                            f"{claim.anchor.year or claim.anchor.century} {claim.anchor.era}"
                            if claim.anchor is not None
                            else "UNANCHORED"
                        ),
                    },
                )
            )
        return out
