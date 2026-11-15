"""DR-5.3.4 Historical Domain Verifier Plugin.

Enforces evidence rules DR5.3.4-H1 through DR5.3.4-H8 behind enable_domain_verifiers=False:
  - H1: Primary archival and peer-reviewed scholarship corroboration with
        anti-transitive syndication collapse.
  - H2: Calendar & era disambiguation (BCE/BC vs. CE/AD, Julian vs. Gregorian)
        preventing false cross-era or calendar-system conflation.
  - H3: Precision-tier separation (circa / c. / fl. / radiocarbon ranges never
        verified as exact single-year dates without qualification).
  - H4: Anachronistic entity / polity attribution guard (flags attributing
        events to modern successor states or institutions prior to founding).
  - H5: Conflicting casualty estimates, dates, or attributions preserved as
        CONTESTED under PRESERVE_CONFLICT_NO_CONSENSUS.
  - H6: Forged, retracted, or authenticity-disputed chronicles/artifacts
        downgraded and never used to corroborate claims.
  - H7: Undated historical claims fail closed on chronological verification.
  - H8: Defense-in-depth PII/token sanitization on archival passages while
        preserving historical years, BCE/CE markers, and treaty/catalog IDs.

Epistemic limitations:
  - Evaluates strictly against retrieved findings (no external archival API).
  - Heuristic era, calendar, and polity-bound extraction; complex multi-calendar
    diplomatic conversions fail closed for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PassageEvidenceRef,
    PublicationStatus,
    SourceClass,
    build_passage_evidence_ref,
)
from src.verifiers.base import ClaimAssessment

HISTORICAL_VERIFIER_VERSION = "dr5.3.4-v1.0"

_YEAR_ERA_RE = re.compile(
    r"\b(?:(c\.|ca\.|circa|fl\.|around|approx\.?)\s*)?"
    r"([0-9]{1,4})"
    r"(?:\s*(BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_JULIAN_RE = re.compile(r"\b(julian|old\s+style|o\.s\.)\b", re.IGNORECASE)
_GREGORIAN_RE = re.compile(r"\b(gregorian|new\s+style|n\.s\.)\b", re.IGNORECASE)
_APPROX_RE = re.compile(
    r"\b(c\.|ca\.|circa|fl\.|around|approximately|radiocarbon|range|between\s+\d{1,4}\s+and\s+\d{1,4})\b",
    re.IGNORECASE,
)
_FORGED_RE = re.compile(
    r"\b(forged|forgery|fabricated|spurious|debunked|hoax|retracted|pseudepigrapha|discredited)\b",
    re.IGNORECASE,
)
_FOUNDED_RE = re.compile(
    r"\b(?:founded|established|formed|unified|incorporated|created|ratified\s+into\s+existence)\s+in\s+([0-9]{3,4})(?:\s*(BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_CASUALTY_OR_COUNT_RE = re.compile(
    r"\b([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.[0-9]+)?\s*(?:million|thousand))\s+"
    r"(?:casualties|dead|killed|deaths|troops|soldiers|inhabitants|signatories|victims)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _TemporalAnchor:
    year: int
    era: str  # "BCE" or "CE"
    is_approx: bool
    calendar: str  # "JULIAN", "GREGORIAN", or "UNSPECIFIED"
    raw_token: str

    @property
    def signed_year(self) -> int:
        return -self.year if self.era == "BCE" else self.year


@dataclass(frozen=True)
class _ParsedHistClaim:
    raw_text: str
    subject: str
    anchor: Optional[_TemporalAnchor]
    count_norm: Optional[str]
    count_token: Optional[str]


def _norm_era(raw_era: Optional[str]) -> str:
    if not raw_era:
        return "CE"
    up = raw_era.strip().upper().rstrip(".")
    return "BCE" if up in {"BCE", "BC"} else "CE"


def _detect_calendar(text: str) -> str:
    has_j = bool(_JULIAN_RE.search(text or ""))
    has_g = bool(_GREGORIAN_RE.search(text or ""))
    if has_j and not has_g:
        return "JULIAN"
    if has_g and not has_j:
        return "GREGORIAN"
    return "UNSPECIFIED"


def _extract_anchor(text: str) -> Optional[_TemporalAnchor]:
    cal = _detect_calendar(text)
    approx_global = bool(_APPROX_RE.search(text or ""))
    for m in _YEAR_ERA_RE.finditer(text or ""):
        prefix, num_str, era_str = m.group(1), m.group(2), m.group(3)
        year = int(num_str)
        if not era_str and not prefix and year < 100:
            continue
        era = _norm_era(era_str)
        is_approx = bool(prefix) or approx_global
        return _TemporalAnchor(
            year=year,
            era=era,
            is_approx=is_approx,
            calendar=cal,
            raw_token=m.group(0).strip(),
        )
    return None


def _norm_count(tok: str) -> str:
    s = re.sub(r"\s+", "", tok.strip().upper()).replace(",", "")
    s = s.replace("MILLION", "M").replace("THOUSAND", "K")
    return s


def _extract_subject(line: str) -> str:
    m = re.match(
        r"^([A-Za-z0-9.'\-\u2013\u2014 ]{2,65}?)\s+"
        r"(?:was|were|occurred|took\s+place|signed|ratified|founded|established|fell|began|ended|caused|resulted|is\s+dated)\b",
        line,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    words = [w.strip(",.:;()") for w in line.split() if w.strip(",.:;()")]
    return " ".join(words[:4]) if words else line


def _extract_historical_claims(report_text: str) -> List[_ParsedHistClaim]:
    claims: List[_ParsedHistClaim] = []
    for raw_line in (report_text or "").splitlines():
        line = raw_line.strip().lstrip("-*?").strip()
        if not line or line.startswith("#"):
            continue
        anchor = _extract_anchor(line)
        cnt_m = _CASUALTY_OR_COUNT_RE.search(line)
        cnt_tok = cnt_m.group(0) if cnt_m else None
        cnt_norm = _norm_count(cnt_m.group(1)) if cnt_m else None
        subject = _extract_subject(line)
        claims.append(
            _ParsedHistClaim(
                raw_text=line,
                subject=subject,
                anchor=anchor,
                count_norm=cnt_norm,
                count_token=cnt_tok,
            )
        )
    return claims


_ABBREV_DOT_RE = re.compile(
    r"\b(c|ca|fl|approx|st|mr|mrs|dr|prof|vol|no|ch|sec|fig|et\s+al|o\.s|n\.s|b\.c\.e|c\.e|b\.c|a\.d)\.",
    re.IGNORECASE,
)


def _sentence_windows(text: str) -> List[str]:
    protected = _ABBREV_DOT_RE.sub(lambda m: m.group(0)[:-1] + "\u2024", text or "")
    parts = re.split(r"(?:(?<!\d)\.(?!\d)|[!?])\s+|\n+", protected)
    return [s.replace("\u2024", ".").strip() for s in parts if s.strip()]


def _subject_tokens(subject: str) -> List[str]:
    stop = {"the", "of", "in", "on", "at", "and", "a", "an", "treaty", "battle", "war", "empire", "kingdom"}
    toks = [t.lower() for t in re.findall(r"[A-Za-z0-9\-]{3,}", subject or "")]
    core = [t for t in toks if t not in stop]
    return core or toks


def _matches_subject(subject: str, sentence: str) -> bool:
    s_low = sentence.lower()
    if subject.lower() in s_low:
        return True
    toks = _subject_tokens(subject)
    if not toks:
        return False
    hits = sum(1 for t in toks if t in s_low)
    return hits >= max(1, (len(toks) + 1) // 2)


class HistoricalVerifierPlugin:
    """Domain verifier for historical chronology, archival treaties, and historiographical claims."""

    name = "historical_verifier"
    domain = "historical"

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() in {
            "historical",
            "history",
            "archival",
            "chronology",
            "historiography",
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
            if claim.anchor is None:
                out.append(
                    ClaimAssessment(
                        claim_text=claim.raw_text,
                        domain=self.domain,
                        plugin_name=self.name,
                        claim_assessment="INSUFFICIENT_EVIDENCE",
                        publication_status=PublicationStatus.NOT_APPLICABLE.value,
                        legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                        source_dependence=DependenceConfidence.UNKNOWN.value,
                        evidence_classification="UNDATED_HISTORICAL_CLAIM",
                        authority_scope_note="Historical claim lacks a chronological date, era, or period anchor; fails closed.",
                        supporting_passages=[],
                        rationale={
                            "source_authority": "Chronological anchor missing from claim",
                            "independence": "Fail-closed on undated historical assertion",
                            "directness": "UNDATED_CHRONOLOGY",
                            "contradictions": "None evaluated without temporal anchor",
                            "evidence_completeness": "Requires explicit year, era (BCE/CE), or bounded historical period",
                        },
                        verifier_version=HISTORICAL_VERIFIER_VERSION,
                    )
                )
                continue

            matching_passages: List[PassageEvidenceRef] = []
            conflicting_passages: List[PassageEvidenceRef] = []
            matched_source_classes: set[str] = set()
            conflict_reasons: List[str] = []
            discredited_detected = False
            anachronism_detected = False
            calendar_mismatch_detected = False
            circa_precision_mismatch = False

            for sa in valid_sources:
                src_text = sa.normalized_text
                if getattr(sa, "retraction_warning", False) or _FORGED_RE.search(src_text):
                    if _matches_subject(claim.subject, src_text):
                        discredited_detected = True
                        conflict_reasons.append("FORGED_OR_RETRACTED_SOURCE")
                        conflicting_passages.append(
                            build_passage_evidence_ref(sa, query_or_claim_term=claim.anchor.raw_token)
                        )
                    continue

                for sent in _sentence_windows(src_text):
                    if not _matches_subject(claim.subject, sent):
                        continue

                    f_m = _FOUNDED_RE.search(sent)
                    if f_m:
                        f_year = int(f_m.group(1))
                        f_era = _norm_era(f_m.group(2))
                        f_signed = -f_year if f_era == "BCE" else f_year
                        if claim.anchor.signed_year < f_signed:
                            anachronism_detected = True
                            conflict_reasons.append("ANACHRONISTIC_POLITY_OR_ENTITY")
                            conflicting_passages.append(
                                build_passage_evidence_ref(sa, query_or_claim_term=f_m.group(0))
                            )
                            continue

                    sent_anchor = _extract_anchor(sent)
                    if sent_anchor is None:
                        continue

                    if (
                        claim.anchor.calendar != "UNSPECIFIED"
                        and sent_anchor.calendar != "UNSPECIFIED"
                        and claim.anchor.calendar != sent_anchor.calendar
                    ):
                        calendar_mismatch_detected = True
                        conflict_reasons.append("CALENDAR_SYSTEM_MISMATCH_JULIAN_VS_GREGORIAN")
                        conflicting_passages.append(
                            build_passage_evidence_ref(sa, query_or_claim_term=sent_anchor.raw_token)
                        )
                        continue

                    if claim.anchor.era != sent_anchor.era or claim.anchor.signed_year != sent_anchor.signed_year:
                        conflict_reasons.append("CHRONOLOGICAL_DATE_OR_ERA_CONFLICT")
                        conflicting_passages.append(
                            build_passage_evidence_ref(sa, query_or_claim_term=sent_anchor.raw_token)
                        )
                        continue

                    if not claim.anchor.is_approx and sent_anchor.is_approx:
                        circa_precision_mismatch = True
                        conflict_reasons.append("CIRCA_APPROXIMATE_VS_EXACT_DATE")
                        conflicting_passages.append(
                            build_passage_evidence_ref(sa, query_or_claim_term=sent_anchor.raw_token)
                        )
                        continue

                    if claim.count_norm:
                        sent_cnt_m = _CASUALTY_OR_COUNT_RE.search(sent)
                        if sent_cnt_m:
                            sent_cnt_norm = _norm_count(sent_cnt_m.group(1))
                            if sent_cnt_norm != claim.count_norm:
                                conflict_reasons.append("CONFLICTING_HISTORICAL_ESTIMATE")
                                conflicting_passages.append(
                                    build_passage_evidence_ref(sa, query_or_claim_term=sent_cnt_m.group(0))
                                )
                                continue

                    matching_passages.append(
                        build_passage_evidence_ref(sa, query_or_claim_term=claim.anchor.raw_token)
                    )
                    matched_source_classes.add(sa.source_class.value)

            if conflicting_passages:
                reason_tag = conflict_reasons[0] if conflict_reasons else "HISTORICAL_CONFLICT"
                if discredited_detected:
                    ev_cls = "DISCREDITED_OR_FORGED_SOURCE"
                    scope_note = "Source flagged as forged, retracted, or authenticity-disputed; fails closed."
                elif anachronism_detected:
                    ev_cls = "ANACHRONISTIC_ENTITY_ATTRIBUTION"
                    scope_note = "Claim attributes historical event to an entity/polity prior to its established founding."
                elif calendar_mismatch_detected:
                    ev_cls = "CALENDAR_SYSTEM_MISMATCH"
                    scope_note = "Julian vs. Gregorian calendar discrepancy detected without explicit conversion alignment."
                elif circa_precision_mismatch:
                    ev_cls = "APPROXIMATE_RANGE_QUALIFIED"
                    scope_note = "Source provides an approximate/circa or radiocarbon range rather than an exact single year."
                else:
                    ev_cls = "CONTESTED_HISTORIOGRAPHY"
                    scope_note = "Conflicting dates, eras, or historical estimates preserved without consensus collapse."

                out.append(
                    ClaimAssessment(
                        claim_text=claim.raw_text,
                        domain=self.domain,
                        plugin_name=self.name,
                        claim_assessment="CONTESTED",
                        publication_status=PublicationStatus.NOT_APPLICABLE.value,
                        legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                        source_dependence=DependenceConfidence.UNKNOWN.value,
                        evidence_classification=ev_cls,
                        authority_scope_note=scope_note,
                        supporting_passages=matching_passages,
                        conflicting_passages=conflicting_passages,
                        rationale={
                            "source_authority": ", ".join(sorted(matched_source_classes)) or "CONFLICTING_SOURCES",
                            "independence": "Suppressed due to historiographical, calendar, or authenticity conflict",
                            "directness": reason_tag,
                            "contradictions": "; ".join(dict.fromkeys(conflict_reasons)),
                            "evidence_completeness": "Competing historical records preserved under PRESERVE_CONFLICT_NO_CONSENSUS",
                        },
                        verifier_version=HISTORICAL_VERIFIER_VERSION,
                    )
                )
                continue

            authoritative_passages = [
                p
                for p in matching_passages
                if p.source_class in {SourceClass.PRIMARY_INSTITUTIONAL.value, SourceClass.PEER_REVIEWED_VENUE.value, SourceClass.ESTABLISHED_SECONDARY.value}
            ]
            if not authoritative_passages:
                out.append(
                    ClaimAssessment(
                        claim_text=claim.raw_text,
                        domain=self.domain,
                        plugin_name=self.name,
                        claim_assessment="INSUFFICIENT_EVIDENCE",
                        publication_status=PublicationStatus.NOT_APPLICABLE.value,
                        legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                        source_dependence=DependenceConfidence.UNKNOWN.value,
                        evidence_classification="UNVERIFIED_OR_UNSUPPORTED",
                        authority_scope_note="No primary archival record or peer-reviewed historical scholarship substantiates this claim.",
                        supporting_passages=matching_passages,
                        rationale={
                            "source_authority": "No Tier 1 archival or Tier 2 peer-reviewed historical source matched",
                            "independence": "UNKNOWN or unverified pop-history/blog venue only (fail-closed)",
                            "directness": "NONE_OR_UNVERIFIED_VENUE",
                            "contradictions": "None detected",
                            "evidence_completeness": "Requires primary archival record or independent peer-reviewed scholarship",
                        },
                        verifier_version=HISTORICAL_VERIFIER_VERSION,
                    )
                )
                continue

            matched_urls = {p.source_url for p in authoritative_passages}
            matched_indep = matched_urls & indep_urls
            has_primary = any(p.source_class == SourceClass.PRIMARY_INSTITUTIONAL.value for p in authoritative_passages)

            if claim.anchor.is_approx:
                ev_cls = "APPROXIMATE_CHRONOLOGY_SUPPORTED"
            elif has_primary and (len(matched_indep) >= 2 or len(matched_urls) >= 2):
                ev_cls = "PRIMARY_CORROBORATED"
            elif has_primary:
                ev_cls = "SINGLE_SOURCE_ARCHIVAL"
            else:
                ev_cls = "SECONDARY_SCHOLARSHIP_SUPPORTED"

            if matched_urls.issubset(unverified_indep_urls) or len(matched_indep) == 0:
                dep_conf = DependenceConfidence.UNKNOWN.value
            else:
                dep_conf = DependenceConfidence.INDEPENDENT.value

            out.append(
                ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="SUPPORTED",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_VERIFIED.value,
                    source_dependence=dep_conf,
                    evidence_classification=ev_cls,
                    authority_scope_note=(
                        f"Historical event anchored to {claim.anchor.year} {claim.anchor.era} "
                        f"(calendar={claim.anchor.calendar}, approx={claim.anchor.is_approx})."
                    ),
                    supporting_passages=authoritative_passages,
                    rationale={
                        "source_authority": ", ".join(sorted(matched_source_classes)),
                        "independence": f"{len(matched_indep)} verified independent origin(s); syndicated copies collapsed",
                        "directness": "ERA_AND_CALENDAR_BOUND",
                        "contradictions": "None detected for this historical event and chronological anchor",
                        "evidence_completeness": "Verified against retrieved archival/scholarly records",
                    },
                    verifier_version=HISTORICAL_VERIFIER_VERSION,
                )
            )

        return out
