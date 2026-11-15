"""DR-5.3.2 Legal & Regulatory Domain Verifier Plugin (v1.2).

Research verification system (not an automated lawyer) enforcing rules DR5.3.2-L1
through DR5.3.2-L7 behind enable_domain_verifiers=False:
  - L1/L2 (Refinement 1): Provision-specific 7-tuple binding:
        (jurisdiction, statute, section, subsection, regulated_entity, operative_date, statutory_version)
        so amendments to subsection (b) never falsely invalidate subsection (c), and
        staggered effective dates / retroactive provisions are strictly enforced.
  - L3: Partial amendments vs. complete repeal/vacatur distinction.
  - L4 (Refinement 2): Strict separation of statutory/regulatory text, party- or
        geographic-limited preliminary injunctions, partial vacatur/remand, nationwide
        facial vacatur, and federal preemption conflicts (never inferring nationwide
        invalidation merely from the word "vacated").
  - L5: Current law vs. historical/undated statutory text discipline (undated statutes
        fail closed to CURRENT_STATUS_NOT_ESTABLISHED).
  - L6 (Refinement 3): Five-criteria circuit-split gate (legal question, statutory
        provision, operative facts, procedural posture, and applicable time period);
        divergences across different postures report PROCEDURAL_OR_POSTURE_DIVERGENCE.
  - L7 (Refinement 4): Subsequent-decision and appellate history detection (en banc,
        Supreme Court reversal, statutory supersession) + PII/credential sanitization.

Epistemic limitations:
  - No live Westlaw KeyCite or Lexis Shepard's citator service.
  - Heuristic provision, jurisdiction, and procedural-posture extraction fails closed
    when ambiguous rather than manufacturing legal certainty.
  - Verifies textual alignment and currency indicators in retrieved authorities,
    never formal legal advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PassageEvidenceRef,
    PublicationStatus,
    SourceClass,
    build_passage_evidence_ref,
)
from src.verifiers.base import ClaimAssessment

LEGAL_VERIFIER_VERSION = "dr5.3.2-v1.2"

_JURISDICTION_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("CALIFORNIA", re.compile(r"\b(california|cal\.?\s*civ\.?\s*code|ccpa|cpra)\b", re.IGNORECASE)),
    ("TEXAS", re.compile(r"\b(texas|tex\.?\s*(?:bus|gov|civ|pen)\.?\s*code)\b", re.IGNORECASE)),
    ("NEW_YORK", re.compile(r"\b(new\s+york|n\.y\.|nydfs)\b", re.IGNORECASE)),
    ("DELAWARE", re.compile(r"\b(delaware|del\.?\s*code|dgcl)\b", re.IGNORECASE)),
    ("FEDERAL", re.compile(r"\b(federal|u\.s\.c\.?|c\.f\.r\.?|united\s+states|congress|scotus|supreme\s+court|epa|sec|ftc|fda|irs)\b", re.IGNORECASE)),
    ("EU", re.compile(r"\b(european\s+union|gdpr|eu\s+ai\s+act)\b", re.IGNORECASE)),
)

_CIRCUIT_RE = re.compile(r"\b(1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|11th|d\.?c\.?|federal)\s+cir(?:cuit|\b)", re.IGNORECASE)
_SECTION_RE = re.compile(r"\b(?:section|sec\.?|\u00a7)\s*([0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)
_SUBSECTION_RE = re.compile(r"(?:\bsubsection\s*[\(\[]?([a-z0-9]{1,3})[\)\]]?|\(([a-z0-9]{1,3})\))", re.IGNORECASE)
_USC_CFR_RE = re.compile(r"\b([0-9]+\s+(?:u\.s\.c\.?|c\.f\.r\.?)\s*(?:\u00a7+\s*)?[0-9]+(?:\.[0-9]+)?(?:\([a-z0-9]+\))*)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_EFFECTIVE_DATE_RE = re.compile(
    r"\beffective\s+((?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+20\d{2}|20\d{2}-\d{2}-\d{2}|20\d{2})\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"\$[0-9,]+(?:\.[0-9]+)?(?:\s*(?:million|billion|thousand))?", re.IGNORECASE)

_ENTITY_SCOPE_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("LARGE_ACCELERATED_FILERS", re.compile(r"\blarge\s+accelerated\s+filers?\b", re.IGNORECASE)),
    ("SMALLER_REPORTING_COMPANIES", re.compile(r"\bsmaller\s+reporting\s+compan(?:y|ies)\b", re.IGNORECASE)),
    ("COVERED_EMPLOYERS", re.compile(r"\bcovered\s+employers?\b", re.IGNORECASE)),
)

_REPEAL_OR_AMEND_RE = re.compile(
    r"\b(repealed|superseded|amended|stricken|abrogated|withdrawn|no\s+longer\s+in\s+effect|sunset)\b",
    re.IGNORECASE,
)
_PARTIAL_AMEND_RE = re.compile(r"\b(partially\s+amended|amended\s+subsection|stricken\s+in\s+part|as\s+modified)\b", re.IGNORECASE)
_RETROACTIVE_RE = re.compile(r"\b(retroactive|retroactively)\b", re.IGNORECASE)
_PREEMPTION_RE = re.compile(r"\b(preempted\s+by\s+federal\s+law|federally\s+preempted|express\s+preemption|conflict\s+preemption)\b", re.IGNORECASE)

_PARTY_LIMITED_INJ_RE = re.compile(
    r"\b(preliminary\s+injunction|limited\s+to\s+(?:named\s+)?plaintiffs|as\s+applied\s+to\s+plaintiffs|enjoined\s+enforcement\s+against\s+(?:named\s+)?plaintiffs|remand\s+without\s+vacatur|partial\s+vacatur)\b",
    re.IGNORECASE,
)
_NATIONWIDE_VACATUR_RE = re.compile(
    r"\b(vacated\s+nationwide|invalidated\s+nationwide|facially\s+unconstitutional|struck\s+down\s+nationwide|set\s+aside\s+in\s+full)\b",
    re.IGNORECASE,
)
_SUBSEQUENT_HISTORY_RE = re.compile(
    r"\b(reversed\s+by|vacated\s+by|overruled\s+by|superseded\s+by\s+statute|rehearing\s+en\s+banc\s+granted|vacated\s+on\s+en\s+banc|reversed\s+by\s+(?:the\s+)?supreme\s+court|en\s+banc\s+(?:reversed|vacated|overruled))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ProvisionTuple:
    raw_text: str
    jurisdiction: Optional[str]
    statute: Optional[str]
    section: Optional[str]
    subsection: Optional[str]
    regulated_entity: Optional[str]
    operative_date: Optional[str]
    statutory_version: Optional[str]
    value_tokens: Tuple[str, ...]
    claims_nationwide_invalidation: bool
    claims_current_controlling: bool
    posture: str


def _detect_jurisdiction(text: str) -> Optional[str]:
    for name, pat in _JURISDICTION_PATTERNS:
        if pat.search(text):
            return name
    return None


def _detect_entity_scope(text: str) -> Optional[str]:
    for name, pat in _ENTITY_SCOPE_PATTERNS:
        if pat.search(text):
            return name
    return None


def _detect_posture(text: str) -> str:
    low = text.lower()
    if "en banc" in low:
        return "EN_BANC_MERITS"
    if "preliminary injunction" in low:
        return "PRELIMINARY_INJUNCTION"
    if "motion to dismiss" in low:
        return "MOTION_TO_DISMISS"
    if "summary judgment" in low:
        return "SUMMARY_JUDGMENT"
    if "merits" in low or "final judgment" in low or "held that" in low or "ruling" in low:
        return "FINAL_MERITS"
    return "UNSPECIFIED"


def _extract_provision_tuple(text: str) -> _ProvisionTuple:
    jur = _detect_jurisdiction(text)
    stat_m = _USC_CFR_RE.search(text)
    statute = re.sub(r"\s+", " ", stat_m.group(1).upper()) if stat_m else None

    sec_m = _SECTION_RE.search(text)
    section = sec_m.group(1) if sec_m else None

    sub_m = _SUBSECTION_RE.search(text)
    subsection = (sub_m.group(1) or sub_m.group(2)).lower() if sub_m else None

    ent = _detect_entity_scope(text)
    eff_m = _EFFECTIVE_DATE_RE.search(text)
    op_date = eff_m.group(1).strip() if eff_m else None

    years = _YEAR_RE.findall(text)
    version = years[-1] if years else None

    vals = tuple(re.sub(r"\s+", "", m.group(0).upper()) for m in _MONEY_RE.finditer(text))
    claims_nw = bool(_NATIONWIDE_VACATUR_RE.search(text))
    claims_curr = bool(re.search(r"\b(current|currently|active|controlling\s+precedent|enforceable|in\s+effect)\b", text, re.IGNORECASE))

    return _ProvisionTuple(
        raw_text=text,
        jurisdiction=jur,
        statute=statute,
        section=section,
        subsection=subsection,
        regulated_entity=ent,
        operative_date=op_date,
        statutory_version=version,
        value_tokens=vals,
        claims_nationwide_invalidation=claims_nw,
        claims_current_controlling=claims_curr,
        posture=_detect_posture(text),
    )


def _sentence_windows(text: str) -> List[str]:
    protected = re.sub(r"\b(U\.S\.C|C\.F\.R|Cir|Sec|No|v|et\s+al)\.", lambda m: m.group(0).replace(".", "<DOT>"), text or "", flags=re.IGNORECASE)
    return [s.replace("<DOT>", ".").strip() for s in re.split(r"(?:(?<!\d)\.(?!\d)|[!?])(?:\s+|$)|\n+", protected) if s.strip()]


def _extract_legal_claims(report_text: str) -> List[_ProvisionTuple]:
    claims: List[_ProvisionTuple] = []
    for raw_line in (report_text or "").splitlines():
        line = raw_line.strip().lstrip("-*?").strip()
        if not line or line.startswith("#"):
            continue
        tup = _extract_provision_tuple(line)
        if tup.jurisdiction or tup.statute or tup.section or tup.value_tokens or tup.claims_nationwide_invalidation or tup.claims_current_controlling:
            claims.append(tup)
    return claims


class LegalVerifierPlugin:
    """Domain verifier for statutes, regulations, judicial decisions, and legal currency."""

    name = "legal_verifier"
    domain = "legal"

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() in {
            "legal",
            "regulatory",
            "law",
            "compliance",
            "statute",
            "policy",
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
        for claim in _extract_legal_claims(report_text):
            supporting: List[PassageEvidenceRef] = []
            conflicting: List[PassageEvidenceRef] = []
            matched_classes: set[str] = set()

            superseded_or_amended = False
            subsequent_reversal_detected = False
            injunction_vs_vacatur_mismatch = False
            preemption_conflict = False
            conflicting_effective_dates: set[str] = set()
            retroactive_or_entity_qualifier = False
            circuit_hits: List[Tuple[str, str, str]] = []  # (circuit, posture, polarity)
            explicit_currency_anchor = False

            for sa in valid_sources:
                src_text = sa.normalized_text
                for sent in _sentence_windows(src_text):
                    s_tup = _extract_provision_tuple(sent)

                    # Rule L1/L4: Federal preemption over state provision surfaces conflict immediately
                    if _PREEMPTION_RE.search(sent) and (
                        (claim.section and s_tup.section and claim.section == s_tup.section)
                        or (claim.statute and s_tup.statute and claim.statute == s_tup.statute)
                        or (claim.jurisdiction and claim.jurisdiction != 'FEDERAL' and _detect_jurisdiction(sent) == claim.jurisdiction)
                    ):
                        preemption_conflict = True
                        conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                        continue

                    # Rule L1: Strict jurisdiction isolation (state != federal, state A != state B)
                    if claim.jurisdiction and s_tup.jurisdiction and claim.jurisdiction != s_tup.jurisdiction:
                        continue

                    # Rule L1/L2 (Refinement 1): Section & subsection isolation
                    if claim.section and s_tup.section and claim.section != s_tup.section:
                        continue
                    all_subs = {(m.group(1) or m.group(2)).lower() for m in _SUBSECTION_RE.finditer(sent)}
                    if claim.subsection and all_subs and claim.subsection not in all_subs:
                        # Amendment to subsection (b) never invalidates subsection (c)
                        continue
                    if claim.statute and s_tup.statute and claim.statute != s_tup.statute:
                        continue

                    # Require at least one structural anchor overlap (jurisdiction, statute, section, or circuit)
                    circuits_in_sent = [m.group(1).upper() for m in _CIRCUIT_RE.finditer(sent)]
                    has_overlap = bool(
                        (claim.jurisdiction and claim.jurisdiction == s_tup.jurisdiction)
                        or (claim.statute and claim.statute == s_tup.statute)
                        or (claim.section and claim.section == s_tup.section)
                        or circuits_in_sent
                    )
                    if not has_overlap:
                        continue

                    # Track effective dates, entity staggering, and retroactive provisions (Refinement 1)
                    if s_tup.operative_date:
                        conflicting_effective_dates.add(s_tup.operative_date.lower())
                    if _RETROACTIVE_RE.search(sent):
                        retroactive_or_entity_qualifier = True
                    if claim.regulated_entity and s_tup.regulated_entity and claim.regulated_entity != s_tup.regulated_entity:
                        retroactive_or_entity_qualifier = True
                        conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                        continue

                    # Refinement 4 (L7): Subsequent appellate reversal / en banc / Supreme Court supersession
                    if _SUBSEQUENT_HISTORY_RE.search(sent):
                        subsequent_reversal_detected = True
                        conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                        continue

                    # Refinement 2 (L4): Distinguish party-limited preliminary injunction / partial vacatur from nationwide vacatur
                    has_party_inj = bool(_PARTY_LIMITED_INJ_RE.search(sent))
                    has_nw_vacatur = bool(_NATIONWIDE_VACATUR_RE.search(sent))
                    if claim.claims_nationwide_invalidation and has_party_inj and not has_nw_vacatur:
                        injunction_vs_vacatur_mismatch = True
                        conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                        continue

                    # Rule L3: Repealed or partially amended provision check
                    local_span = sent
                    if claim.subsection and len(all_subs) > 1:
                        sub_pos = sent.lower().find(f'subsection {claim.subsection}')
                        if sub_pos < 0:
                            sub_pos = sent.lower().find(f'subsection ({claim.subsection})')
                        if sub_pos >= 0:
                            local_span = sent[sub_pos:sub_pos + 110]
                    has_repeal_amend = bool(_REPEAL_OR_AMEND_RE.search(local_span))
                    if has_repeal_amend:
                        # If claim asserts an older version/value or unqualified current law when amended/repealed
                        if claim.value_tokens and s_tup.value_tokens and set(claim.value_tokens) != set(s_tup.value_tokens):
                            superseded_or_amended = True
                            conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                            continue
                        if claim.statutory_version and s_tup.statutory_version and claim.statutory_version < s_tup.statutory_version:
                            superseded_or_amended = True
                            conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                            continue
                        if not claim.statutory_version or _PARTIAL_AMEND_RE.search(sent) or re.search(r"\brepealed\b", sent, re.IGNORECASE):
                            superseded_or_amended = True
                            conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                            continue

                    # Value / threshold contradiction on same provision
                    if claim.value_tokens and s_tup.value_tokens and set(claim.value_tokens) != set(s_tup.value_tokens):
                        conflicting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                        continue

                    # Refinement 3 (L6): Track appellate circuit rulings and procedural posture
                    if circuits_in_sent:
                        polarity = "INVALID_OR_ENJOINED" if re.search(r"\b(enjoined|unlawful|invalid|invalidated|vacated|barred|exceeded)\b", sent, re.IGNORECASE) else "UPHELD_OR_PERMITTED"
                        for c_name in circuits_in_sent:
                            circuit_hits.append((c_name, s_tup.posture, polarity))

                    # Current-effectiveness anchor check (L5)
                    if sa.publication_date or sa.last_updated_date or s_tup.operative_date or re.search(r"\b(currently\s+in\s+effect|as\s+of\s+20\d{2})\b", sent, re.IGNORECASE):
                        explicit_currency_anchor = True

                    supporting.append(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                    matched_classes.add(sa.source_class.value)

            # Evaluate Refinement 3 (L6): Circuit split vs. procedural divergence
            distinct_circuits = {c for c, _, _ in circuit_hits}
            distinct_polarities = {pol for _, _, pol in circuit_hits}
            distinct_postures = {pos for _, pos, _ in circuit_hits if pos != "UNSPECIFIED"}
            circuit_conflict = len(distinct_circuits) >= 2 and len(distinct_polarities) >= 2
            is_true_circuit_split = (
                circuit_conflict
                and len(distinct_postures) == 1
                and len(conflicting_effective_dates) <= 1
            )

            if circuit_conflict:
                all_circuit_passages = supporting + conflicting
                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="CONTESTED",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                    source_dependence=DependenceConfidence.INDEPENDENT.value,
                    evidence_classification=(
                        "ESTABLISHED_CIRCUIT_SPLIT"
                        if is_true_circuit_split
                        else "PROCEDURAL_OR_POSTURE_DIVERGENCE"
                    ),
                    authority_scope_note=(
                        "Appellate circuits conflict on identical legal question, provision, facts, procedural posture, and period."
                        if is_true_circuit_split
                        else f"Competing appellate rulings differ in procedural posture ({', '.join(sorted(distinct_postures)) or 'MIXED'}); reported as procedural divergence rather than an established circuit split."
                    ),
                    supporting_passages=[],
                    conflicting_passages=all_circuit_passages,
                    rationale={
                        "source_authority": ", ".join(sorted(matched_classes)) or "PRIMARY_INSTITUTIONAL",
                        "independence": "Distinct appellate circuit courts preserved independently",
                        "directness": "ESTABLISHED_CIRCUIT_SPLIT" if is_true_circuit_split else "PROCEDURAL_OR_POSTURE_DIVERGENCE",
                        "contradictions": "Competing judicial holdings preserved under PRESERVE_CONFLICT_NO_CONSENSUS",
                        "evidence_completeness": "No live Shepard's/KeyCite citator; posture and period checked from retrieved corpus",
                    },
                    verifier_version=LEGAL_VERIFIER_VERSION,
                ))
                continue

            # Evaluate Refinement 4 (L7), Refinement 2 (L4), L3, L1/L2 conflicts
            if subsequent_reversal_detected or superseded_or_amended or injunction_vs_vacatur_mismatch or preemption_conflict or len(conflicting_effective_dates) > 1 or conflicting:
                if subsequent_reversal_detected or superseded_or_amended:
                    curr_val = LegalCurrencyStatus.AMENDED_OR_SUPERSEDED.value
                    ev_cls = "SUPERSEDED_OR_REVERSED_AUTHORITY"
                elif injunction_vs_vacatur_mismatch:
                    curr_val = LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
                    ev_cls = "INJUNCTION_VS_VACATUR_SCOPE_MISMATCH"
                elif preemption_conflict:
                    curr_val = LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
                    ev_cls = "FEDERAL_PREEMPTION_CONFLICT"
                else:
                    curr_val = LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
                    ev_cls = "CONTESTED_LEGAL_AUTHORITY"

                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="CONTESTED",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=curr_val,
                    source_dependence=DependenceConfidence.UNKNOWN.value,
                    evidence_classification=ev_cls,
                    authority_scope_note=(
                        "Judicial decision vs. statutory/regulatory authority distinguished; "
                        "subsequent history, scope limits, or provision amendments prevent unqualified verification."
                    ),
                    supporting_passages=supporting,
                    conflicting_passages=conflicting,
                    rationale={
                        "source_authority": ", ".join(sorted(matched_classes)) or "CONFLICTING_LEGAL_SOURCES",
                        "independence": "Competing authorities preserved without majority consensus",
                        "directness": ev_cls,
                        "contradictions": "Subsequent reversal, amendment, preemption, or injunction/vacatur scope mismatch detected",
                        "evidence_completeness": "Admits legal uncertainty rather than manufacturing certainty",
                    },
                    verifier_version=LEGAL_VERIFIER_VERSION,
                ))
                continue

            # Tier 3 / Unverified venues cannot independently substantiate legal claims
            authoritative = [
                p for p in supporting
                if p.source_class in {
                    SourceClass.PRIMARY_INSTITUTIONAL.value,
                    SourceClass.ESTABLISHED_SECONDARY.value,
                    SourceClass.INDEX_OR_RESOLVER.value,
                }
            ]
            if not authoritative:
                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="INSUFFICIENT_EVIDENCE",
                    publication_status=PublicationStatus.NOT_APPLICABLE.value,
                    legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                    source_dependence=DependenceConfidence.UNKNOWN.value,
                    evidence_classification="UNVERIFIED_OR_UNSUPPORTED",
                    authority_scope_note="No authoritative statutory, regulatory, or judicial source in the matching jurisdiction/provision substantiates this claim.",
                    supporting_passages=supporting,
                    rationale={
                        "source_authority": "No Tier 1/Tier 2 authority within matching jurisdiction and provision",
                        "independence": "UNKNOWN or unverified venue only (fail-closed)",
                        "directness": "JURISDICTION_OR_PROVISION_UNMATCHED",
                        "contradictions": "None detected",
                        "evidence_completeness": "Fail-closed; no live Westlaw/Lexis citator",
                    },
                    verifier_version=LEGAL_VERIFIER_VERSION,
                ))
                continue

            # Rule L5: Undated or historical statutory text never defaults to CURRENT_VERIFIED
            is_historical_year = bool(claim.statutory_version and int(claim.statutory_version) < 2024 and not explicit_currency_anchor)
            if explicit_currency_anchor and not is_historical_year and not retroactive_or_entity_qualifier:
                currency_status = LegalCurrencyStatus.CURRENT_VERIFIED.value
            else:
                currency_status = LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value

            matched_urls = {p.source_url for p in authoritative}
            matched_indep = matched_urls & indep_urls
            has_primary = any(p.source_class == SourceClass.PRIMARY_INSTITUTIONAL.value for p in authoritative)

            if has_primary and (len(matched_indep) >= 2 or len(matched_urls) >= 2):
                ev_cls = "PRIMARY_CORROBORATED"
            elif has_primary:
                ev_cls = "SINGLE_SOURCE_AUTHORITATIVE"
            else:
                ev_cls = "SECONDARY_LEGAL_REPOSITORY"

            dep_conf = (
                DependenceConfidence.UNKNOWN.value
                if (matched_urls.issubset(unverified_indep_urls) or len(matched_indep) == 0)
                else DependenceConfidence.INDEPENDENT.value
            )

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
                    "Provision-specific authority matched within retrieved corpus; "
                    + (
                        "currency anchored by explicit effective/publication date."
                        if currency_status == LegalCurrencyStatus.CURRENT_VERIFIED.value
                        else "current legal status not established (undated/historical text or entity/retroactive qualifier; no live Shepard's/KeyCite)."
                    )
                ),
                supporting_passages=authoritative,
                rationale={
                    "source_authority": ", ".join(sorted(matched_classes)),
                    "independence": f"{len(matched_indep)} verified independent origin(s)",
                    "directness": "PROVISION_AND_JURISDICTION_BOUND",
                    "contradictions": "None detected within retrieved corpus",
                    "evidence_completeness": "Verified against retrieved authorities only (not formal legal advice)",
                },
                verifier_version=LEGAL_VERIFIER_VERSION,
            ))

        return out
