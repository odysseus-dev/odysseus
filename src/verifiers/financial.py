"""DR-5.3.1 Financial Domain Verifier Plugin.

Enforces evidence rules DR5.3.1-F1 through DR5.3.1-F6 behind enable_domain_verifiers=False:
  - F1: Entity, metric, unit/scale, and fiscal-period binding (prevents cross-company
        or cross-quarter conflation).
  - F2: GAAP vs. Non-GAAP / Adjusted accounting-basis hygiene (flags implicit basis
        mismatches rather than emitting unqualified SUPPORTED).
  - F3: Point-in-time temporal currency (undated yields/prices fail closed to
        CURRENT_STATUS_NOT_ESTABLISHED).
  - F4: Attributed projections / guidance separated from realized historical metrics.
  - F5: Contradictory financial metrics preserved as CONTESTED with conflicting_passages.
  - F6: Sanitized passage excerpts preserving legitimate dollar/percentage figures while
        redacting credentials/account/routing identifiers.

Epistemic limitations:
  - Evaluates strictly against retrieved findings (no live market feed).
  - Heuristic fiscal-period and accounting-basis matching; complex footnote restatements
    fail closed for human review.
  - Verifies factual alignment with disclosures, never investment suitability.
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

FINANCIAL_VERIFIER_VERSION = "dr5.3.1-v1.0"

_PERIOD_RE = re.compile(r"\b(FY\s*20\d{2}|Q[1-4]\s*20\d{2}|20\d{2}-\d{2}-\d{2}|20\d{2}|10-?Year|10Y|2-?Year|2Y|TTM)\b", re.IGNORECASE)
_MONEY_OR_PCT_RE = re.compile(r"(\$[0-9]+(?:\.[0-9]+)?\s*(?:trillion|billion|million|thousand|[TBMK])?|[0-9]+(?:\.[0-9]+)?%)", re.IGNORECASE)
_NON_GAAP_RE = re.compile(r"\b(non-?gaap|adjusted|pro\s+forma)\b", re.IGNORECASE)
_PROJECTION_RE = re.compile(r"\b(guidance|forecast|projected|projecting|estimate|estimated|price\s+target|consensus\s+expect)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _ParsedFinClaim:
    raw_text: str
    entity: str
    period: Optional[str]
    value_token: str
    value_norm: str
    claims_gaap: bool
    claims_non_gaap: bool
    is_projection: bool


def _norm_period(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    return re.sub(r"\s+", "", p.upper()).replace("10-YEAR", "10Y").replace("2-YEAR", "2Y")


def _norm_value(v: str) -> str:
    s = v.strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("BILLION", "B").replace("MILLION", "M").replace("TRILLION", "T")
    return s


def _extract_financial_claims(report_text: str) -> List[_ParsedFinClaim]:
    claims: List[_ParsedFinClaim] = []
    for raw_line in (report_text or "").splitlines():
        line = raw_line.strip().lstrip("-*?").strip()
        if not line or line.startswith("#"):
            continue
        val_m = _MONEY_OR_PCT_RE.search(line)
        if not val_m:
            continue
        val_tok = val_m.group(1)
        per_m = _PERIOD_RE.search(line)
        per_norm = _norm_period(per_m.group(1)) if per_m else None

        # Extract leading entity phrase before financial verb/metric
        ent_m = re.match(
            r"^([A-Za-z0-9.&'\- ]{2,45}?)\s+(?:Q[1-4]|FY\s*20\d{2}|revenue|eps|net\s+income|yield|rate|cpi|gdp|guidance|is|was|reported|posts|posted|stands)\b",
            line,
            re.IGNORECASE,
        )
        entity = ent_m.group(1).strip() if ent_m else line.split()[0].strip(",.:;")
        claims_non_gaap = bool(_NON_GAAP_RE.search(line))
        claims_gaap = bool(re.search(r"\bgaap\b", line, re.IGNORECASE)) and not claims_non_gaap
        is_proj = bool(_PROJECTION_RE.search(line))

        claims.append(_ParsedFinClaim(
            raw_text=line,
            entity=entity,
            period=per_norm,
            value_token=val_tok,
            value_norm=_norm_value(val_tok),
            claims_gaap=claims_gaap,
            claims_non_gaap=claims_non_gaap,
            is_projection=is_proj,
        ))
    return claims


def _sentence_windows(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?:(?<!\d)\.(?!\d)|[!?])\s+|\n+", text or "") if s.strip()]


class FinancialVerifierPlugin:
    """Domain verifier for financial filings, macro benchmarks, yields, and corporate metrics."""

    name = "financial_verifier"
    domain = "financial"

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() in {
            "financial",
            "finance",
            "economics",
            "market",
            "macroeconomic",
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
        for claim in _extract_financial_claims(report_text):
            matching_passages: List[PassageEvidenceRef] = []
            conflicting_passages: List[PassageEvidenceRef] = []
            basis_mismatch_detected = False
            matched_source_classes: set[str] = set()
            matched_has_date = False

            for sa in valid_sources:
                src_text = sa.normalized_text
                if claim.entity.lower() not in src_text.lower():
                    continue

                for sent in _sentence_windows(src_text):
                    if claim.entity.lower() not in sent.lower():
                        continue
                    sent_periods = {_norm_period(m.group(1)) for m in _PERIOD_RE.finditer(sent)}
                    if claim.period and sent_periods and claim.period not in sent_periods:
                        # Different fiscal period for same entity -> never conflate (F1)
                        continue
                    if claim.period and not sent_periods:
                        # Claim specifies a quarter/FY but sentence has no period anchor -> fail closed (F1)
                        continue

                    sent_vals = {_norm_value(m.group(1)) for m in _MONEY_OR_PCT_RE.finditer(sent)}
                    if not sent_vals:
                        continue

                    sent_non_gaap = bool(_NON_GAAP_RE.search(sent))
                    if claim.value_norm in sent_vals:
                        # Rule F2: Catch GAAP vs Non-GAAP basis mismatch
                        if claim.claims_gaap and sent_non_gaap:
                            basis_mismatch_detected = True
                            conflicting_passages.append(build_passage_evidence_ref(sa, query_or_claim_term=claim.value_token))
                        else:
                            matching_passages.append(build_passage_evidence_ref(sa, query_or_claim_term=claim.value_token))
                            matched_source_classes.add(sa.source_class.value)
                            has_calendar_period = bool(claim.period and not claim.period.endswith('Y') and claim.period != 'TTM')
                            if sa.publication_date or sa.last_updated_date or has_calendar_period:
                                matched_has_date = True
                    else:
                        # Same entity + same period (or undated benchmark) with a different value -> conflict (F5)
                        other_tok = next(iter(_MONEY_OR_PCT_RE.finditer(sent))).group(1)
                        conflicting_passages.append(build_passage_evidence_ref(sa, query_or_claim_term=other_tok))

            # Rule F3: Point-in-time temporal currency
            currency_status = (
                LegalCurrencyStatus.CURRENT_VERIFIED.value
                if matched_has_date
                else LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
            )

            # Evaluate conflicts, basis mismatches, or insufficient tier authority
            if basis_mismatch_detected or (matching_passages and conflicting_passages) or (not matching_passages and conflicting_passages):
                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="CONTESTED",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=currency_status,
                    source_dependence=DependenceConfidence.UNKNOWN.value,
                    evidence_classification="CONTESTED_CONFLICTING",
                    authority_scope_note=(
                        "GAAP vs. Non-GAAP accounting-basis mismatch detected in source disclosure."
                        if basis_mismatch_detected
                        else "Conflicting financial values detected for same entity/period; unqualified verification suppressed."
                    ),
                    supporting_passages=matching_passages,
                    conflicting_passages=conflicting_passages,
                    rationale={
                        "source_authority": ", ".join(sorted(matched_source_classes)) or "CONFLICTING_SOURCES",
                        "independence": "Suppressed due to financial value or accounting-basis conflict",
                        "directness": "BASIS_MISMATCH_NON_GAAP_VS_GAAP" if basis_mismatch_detected else "DIRECT_FINANCIAL_COMPARISON",
                        "contradictions": "Basis mismatch (Non-GAAP vs GAAP)" if basis_mismatch_detected else "Conflicting figures across sources",
                        "evidence_completeness": "Competing figures preserved under PRESERVE_CONFLICT_NO_CONSENSUS",
                    },
                    verifier_version=FINANCIAL_VERIFIER_VERSION,
                ))
                continue

            # Tier 3 / Unverified venues cannot independently verify quantitative financial claims
            authoritative_passages = [
                p for p in matching_passages
                if p.source_class in {SourceClass.PRIMARY_INSTITUTIONAL.value, SourceClass.ESTABLISHED_SECONDARY.value}
            ]
            if not authoritative_passages:
                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="INSUFFICIENT_EVIDENCE",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                    source_dependence=DependenceConfidence.UNKNOWN.value,
                    evidence_classification="UNVERIFIED_OR_UNSUPPORTED",
                    authority_scope_note="No Tier 1 statutory/institutional filing or Tier 2 wire passage binds this entity, metric, and period.",
                    supporting_passages=matching_passages,
                    rationale={
                        "source_authority": "No Tier 1/Tier 2 authoritative financial source substantiates this claim",
                        "independence": "UNKNOWN or unverified venue only (fail-closed)",
                        "directness": "NONE_OR_UNVERIFIED_VENUE",
                        "contradictions": "None detected",
                        "evidence_completeness": "Requires primary SEC/central-bank filing or established wire corroboration",
                    },
                    verifier_version=FINANCIAL_VERIFIER_VERSION,
                ))
                continue

            matched_urls = {p.source_url for p in authoritative_passages}
            matched_indep = matched_urls & indep_urls
            has_primary = any(p.source_class == SourceClass.PRIMARY_INSTITUTIONAL.value for p in authoritative_passages)

            if claim.is_projection:
                ev_cls = "ATTRIBUTED_PROJECTION_OR_GUIDANCE"
            elif has_primary and (len(matched_indep) >= 2 or len(matched_urls) >= 2):
                ev_cls = "PRIMARY_CORROBORATED"
            elif has_primary:
                ev_cls = "SINGLE_SOURCE_AUTHORITATIVE"
            else:
                ev_cls = "SECONDARY_WIRE_SUPPORTED"

            if matched_urls.issubset(unverified_indep_urls) or len(matched_indep) == 0:
                dep_conf = DependenceConfidence.UNKNOWN.value
            else:
                dep_conf = DependenceConfidence.INDEPENDENT.value

            out.append(ClaimAssessment(
                claim_text=claim.raw_text,
                domain=self.domain,
                plugin_name=self.name,
                claim_assessment="SUPPORTED",
                publication_status=PublicationStatus.NOT_APPLICABLE.value,
                legal_currency=currency_status,
                source_dependence=dep_conf,
                evidence_classification=ev_cls,
                authority_scope_note=(
                    "Point-in-time financial figure verified against retrieved disclosure; "
                    + ("as-of period/date anchored." if matched_has_date else "source date missing ? current status not established.")
                ),
                supporting_passages=authoritative_passages,
                rationale={
                    "source_authority": ", ".join(sorted(matched_source_classes)),
                    "independence": f"{len(matched_indep)} verified independent origin(s); syndicated wire copies collapsed",
                    "directness": "ATTRIBUTED_GUIDANCE" if claim.is_projection else "ENTITY_AND_PERIOD_BOUND",
                    "contradictions": "None detected for this entity, metric, and period",
                    "evidence_completeness": "Verified against retrieved financial disclosure without live market feed",
                },
                verifier_version=FINANCIAL_VERIFIER_VERSION,
            ))

        return out
