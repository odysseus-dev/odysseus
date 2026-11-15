"""DR-5.3.3 Medical & Scientific Domain Verifier Plugin (v1.2).

Research verification system (not a clinician or prescriber) enforcing rules
DR5.3.3-M1 through DR5.3.3-M7 behind enable_domain_verifiers=False:
  - M1: Clinical trials vs. observational studies (observational associations
        never substantiate causal or curative efficacy claims).
  - M2: Non-automatic clinical evidence hierarchy with sample-size/statistical-
        power, study-design/blinding, risk-of-bias, temporal-date, and
        population/endpoint alignment checks (systematic reviews never
        automatically override newer Phase 3 RCTs; disagreements stay CONTESTED).
  - M3: Preprints (medRxiv, bioRxiv, unreviewed) never classified as peer-reviewed
        proof (publication_status = PREPRINT_NOT_PEER_REVIEWED).
  - M4: Three-way publication integrity separation:
        * RETRACTED -> excluded completely (never SUPPORTED).
        * EXPRESSION_OF_CONCERN -> flagged (CONTESTED).
        * CORRECTED_WITH_ERRATUM -> permitted ONLY when linked to original
          publication (DOI/PMID/NCT/title) AND post-correction passage confirms
          the exact reported figure.
  - M5: Human clinical trials vs. animal (murine/primate/xenograft) and in-vitro
        preclinical research isolation (preclinical never proves human efficacy).
  - M6: Six-axis clinical scope tuple:
        (intervention, dosage, route, population, endpoint, study_design)
        evaluated independently and jointly, with publication_year/guideline_date
        maintained as a separate temporal currency dimension.
  - M7: Conflicting clinical guidelines preserved as CONTESTED without recency
        override unless formally withdrawn/superseded, plus defense-in-depth
        PHI minimization (MRN, patient/subject ID, DOB, patient name+clinical
        context, address/contact info, OCR-corrupted identifiers) while preserving
        dosages, sample sizes, hazard ratios, p-values, and NCT IDs.

Epistemic & Security Disclosures:
  - No live PubMed, FDA MedWatch, or Retraction Watch API access.
  - Regex/heuristic PHI redaction is a defense-in-depth minimization measure,
    NOT a HIPAA Safe Harbor or Expert Determination compliance guarantee.
  - Evaluates textual alignment and methodological boundaries within retrieved
    literature only; never constitutes diagnosis, prescription, or medical advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, List, Mapping, Optional, Sequence, Set, Tuple

from src.research_sources import (
    DependenceConfidence,
    LegalCurrencyStatus,
    PassageEvidenceRef,
    SourceClass,
    build_passage_evidence_ref,
)
from src.verifiers.base import ClaimAssessment

MEDICAL_VERIFIER_VERSION = "dr5.3.3-v1.2"

_NON_MEDICAL_ADVICE_NOTE = (
    "Research verification only (not clinical diagnosis or medical advice); "
    "defense-in-depth PHI minimization applied (not HIPAA compliance certification)."
)

_DOSE_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)?\s*(?:mg|mcg|ug|g|ml|iu|units?))\b", re.IGNORECASE)
_SAMPLE_SIZE_RE = re.compile(r"\bn\s*=\s*([0-9,]+)\b", re.IGNORECASE)
_NCT_RE = re.compile(r"\b(NCT[0-9]{8})\b", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID\s*:?\s*([0-9]{5,10})\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.[0-9]{4,9}/[^\s,;]+)\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_ROUTE_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("ORAL", re.compile(r"\b(oral|orally|by\s+mouth|po|tablet|capsule)\b", re.IGNORECASE)),
    ("INTRAVENOUS", re.compile(r"\b(intravenous|intravenously|iv|infusion)\b", re.IGNORECASE)),
    ("SUBCUTANEOUS", re.compile(r"\b(subcutaneous|subcutaneously|sc|subq)\b", re.IGNORECASE)),
    ("TOPICAL", re.compile(r"\b(topical|topically|transdermal)\b", re.IGNORECASE)),
)

_POPULATION_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("PEDIATRIC", re.compile(r"\b(pediatric|paediatric|children|adolescents?|infants?|neonates?)\b", re.IGNORECASE)),
    ("PREGNANT", re.compile(r"\b(pregnant|pregnancy|gestational|maternal)\b", re.IGNORECASE)),
    ("GERIATRIC", re.compile(r"\b(geriatric|elderly|older\s+adults?)\b", re.IGNORECASE)),
    ("RENAL_IMPAIRMENT", re.compile(r"\b(renal\s+impairment|chronic\s+kidney\s+disease|ckd|dialysis)\b", re.IGNORECASE)),
    ("ADULT", re.compile(r"\b(adults?|adult\s+patients?)\b", re.IGNORECASE)),
)

_ENDPOINT_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("HARD_CLINICAL", re.compile(r"\b(all-cause\s+mortality|mortality|overall\s+survival|stroke|myocardial\s+infarction|hospitalization|clinical\s+cure|disease-free\s+survival)\b", re.IGNORECASE)),
    ("SURROGATE_BIOMARKER", re.compile(r"\b(surrogate|biomarker|ldl(?:\s+cholesterol)?|hba1c|tumor\s+(?:shrinkage|marker)|blood\s+pressure\s+level|viral\s+load)\b", re.IGNORECASE)),
)

_PRECLINICAL_ANIMAL_RE = re.compile(
    r"\b(mice|mouse|murine|rats?|rodents?|primate|macaque|zebrafish|xenograft|animal\s+model)\b",
    re.IGNORECASE,
)
_PRECLINICAL_IN_VITRO_RE = re.compile(
    r"\b(in\s+vitro|cell\s+line|cell\s+culture|hela|petri|organoid|assay\s+plate)\b",
    re.IGNORECASE,
)
_HUMAN_CLINICAL_RE = re.compile(
    r"\b(human\s+patients?|humans?|clinical\s+trial|patients?\s+with|phase\s+[1234]|randomized\s+controlled)\b",
    re.IGNORECASE,
)

_OBSERVATIONAL_RE = re.compile(
    r"\b(observational|cohort\s+study|retrospective|case-control|cross-sectional|associated\s+with|correlation)\b",
    re.IGNORECASE,
)
_CAUSAL_CLAIM_RE = re.compile(
    r"\b(causally\s+prevents?|prevents?\s+and\s+cures?|cures?|proves?\s+causal|clinically\s+proven\s+to\s+treat)\b",
    re.IGNORECASE,
)

_SYSTEMATIC_REVIEW_RE = re.compile(r"\b(systematic\s+review|meta-analysis|cochrane)\b", re.IGNORECASE)
_RCT_RE = re.compile(r"\b(phase\s+3|phase\s+iii|randomized\s+controlled\s+trial|double-blind\s+rct|multicenter\s+rct)\b", re.IGNORECASE)
_PILOT_RE = re.compile(r"\b(pilot\s+study|single-center|preliminary\s+case\s+series|open-label\s+pilot)\b", re.IGNORECASE)
_CONSENSUS_CLAIM_RE = re.compile(r"\b(established\s+medical\s+consensus|definitively\s+proven|universal\s+standard)\b", re.IGNORECASE)

_RETRACTED_RE = re.compile(r"\b(retracted|retraction\s+notice|withdrawn\s+due\s+to)\b", re.IGNORECASE)
_EOC_RE = re.compile(r"\b(expression\s+of\s+concern|editorial\s+expression\s+of\s+concern)\b", re.IGNORECASE)
_ERRATUM_RE = re.compile(r"\b(corrigendum|erratum|published\s+correction|corrected\s+value)\b", re.IGNORECASE)
_PREPRINT_RE = re.compile(r"\b(preprint|medrxiv|biorxiv|not\s+yet\s+peer-reviewed)\b", re.IGNORECASE)
_PEER_REVIEW_CLAIM_RE = re.compile(r"\b(peer-reviewed\s+clinical\s+trial|peer-reviewed\s+proof)\b", re.IGNORECASE)

_GUIDELINE_ORG_RE = re.compile(r"\b(uspstf|aha|acc|acs|idsa|nccn|who|cdc|guideline|screening\s+recommendation)\b", re.IGNORECASE)

_BIAS_QUALITY_RE = re.compile(
    r"\b(double-blind|placebo-controlled|multicenter|underpowered|open-label|high\s+attrition|post-hoc|industry-sponsored)\b",
    re.IGNORECASE,
)

_TRUSTED_BIOMEDICAL_DOMAINS: Tuple[str, ...] = (
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "clinicaltrials.gov",
    "fda.gov",
    "ema.europa.eu",
    "who.int",
    "cdc.gov",
    "nih.gov",
    "cochranelibrary.com",
    "nejm.org",
    "thelancet.com",
    "jamanetwork.com",
    "bmj.com",
    "nature.com",
    "science.org",
    "cell.com",
    "uspreventiveservicestaskforce.org",
    "heart.org",
    "nccn.org",
    "idsociety.org",
)

_PHI_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bM\s*R\s*N\s*[:#-]?\s*[A-Z0-9\s-]{5,18}\b", re.IGNORECASE), "[REDACTED_MRN]"),
    (re.compile(r"\b(?:patient|subject)\s*(?:id|identifier|#)\s*[:#-]?\s*[A-Z0-9-]{4,16}\b", re.IGNORECASE), "[REDACTED_PATIENT_ID]"),
    (re.compile(r"\b(?:dob|date\s+of\s+birth)\s*[:#-]?\s*(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b", re.IGNORECASE), "[REDACTED_DOB]"),
    (
        re.compile(
            r"\bpatient\s+(?!with\b|and\b|or\b|in\b|for\b|who\b|on\b|at\b|to\b|of\b|population\b|group\b|cohort\b)[a-z]{2,}\s+"
            r"(?!with\b|and\b|or\b|in\b|for\b|who\b|on\b|at\b|to\b|of\b|population\b|group\b|cohort\b)[a-z]{2,}\b",
            re.IGNORECASE,
        ),
        "patient [REDACTED_PATIENT_NAME]",
    ),
    (re.compile(r"\b\d{1,5}\s+[A-Za-z0-9.\s]{2,25}\s+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln)\b", re.IGNORECASE), "[REDACTED_ADDRESS]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\bS\s*S\s*N\s*[:#-]?\s*\d[\d\s-]{7,13}\d\b", re.IGNORECASE), "[REDACTED_SSN]"),
)


def sanitize_clinical_phi_defense_in_depth(text: str) -> str:
    """Defense-in-depth PHI minimization (NOT a HIPAA Safe Harbor certification)."""
    out = text or ""
    for pat, repl in _PHI_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _sanitize_passage_ref(ref: PassageEvidenceRef) -> PassageEvidenceRef:
    clean_excerpt = sanitize_clinical_phi_defense_in_depth(ref.excerpt)
    if clean_excerpt == ref.excerpt:
        return ref
    return replace(
        ref,
        excerpt=clean_excerpt,
        char_end=min(ref.char_end, ref.char_start + len(clean_excerpt)),
    )


@dataclass(frozen=True)
class _SixAxisClinicalScope:
    """Six clinical dimensions (temporal publication_year is tracked separately)."""
    intervention: Optional[str]
    dosage: Optional[str]
    route: Optional[str]
    population: Optional[str]
    endpoint: Optional[str]
    study_design: str


@dataclass(frozen=True)
class _TemporalContext:
    """Separate temporal currency dimension (never mixed into the 6 clinical axes)."""
    publication_year: Optional[str]
    explicit_date_anchor: bool


@dataclass(frozen=True)
class _MedicalRecord:
    raw_text: str
    scope: _SixAxisClinicalScope
    temporal: _TemporalContext
    translational_stage: str  # HUMAN_CLINICAL | ANIMAL_PRECLINICAL | IN_VITRO | UNSPECIFIED
    sample_size: Optional[int]
    quality_cues: Tuple[str, ...]
    nct_ids: Tuple[str, ...]
    pmid_ids: Tuple[str, ...]
    dois: Tuple[str, ...]
    claims_causal: bool
    claims_consensus: bool
    claims_peer_reviewed: bool
    is_guideline: bool
    polarity: str  # POSITIVE_EFFECTIVE | NEGATIVE_INEFFECTIVE | NEUTRAL


def _detect_first(text: str, patterns: Tuple[Tuple[str, re.Pattern[str]], ...]) -> Optional[str]:
    for label, pat in patterns:
        if pat.search(text):
            return label
    return None


def _detect_study_design(text: str) -> str:
    if _SYSTEMATIC_REVIEW_RE.search(text):
        return "SYSTEMATIC_REVIEW_META_ANALYSIS"
    if _RCT_RE.search(text):
        return "RANDOMIZED_CONTROLLED_TRIAL"
    if _PILOT_RE.search(text):
        return "SMALL_PILOT_STUDY"
    if _OBSERVATIONAL_RE.search(text):
        return "OBSERVATIONAL_STUDY"
    if _PRECLINICAL_ANIMAL_RE.search(text) or _PRECLINICAL_IN_VITRO_RE.search(text):
        return "PRECLINICAL_STUDY"
    return "UNSPECIFIED"


def _detect_translational_stage(text: str) -> str:
    if _PRECLINICAL_IN_VITRO_RE.search(text):
        return "IN_VITRO"
    if _PRECLINICAL_ANIMAL_RE.search(text):
        return "ANIMAL_PRECLINICAL"
    if _HUMAN_CLINICAL_RE.search(text) or _detect_first(text, _POPULATION_PATTERNS):
        return "HUMAN_CLINICAL"
    return "UNSPECIFIED"


def _extract_intervention(text: str) -> Optional[str]:
    cap_m = re.search(
        r"\b((?:drug|compound|agent|therapy|vaccine|statin|herbal\s+extract)[-\s]+[a-z0-9]+|[a-z]{4,}(?:mab|nib|vir|statin|pril|sartan|olol|prazole|zumab|ximab))\b",
        text,
        re.IGNORECASE,
    )
    if cap_m:
        return re.sub(r"[-\s]+", "-", cap_m.group(1).upper())
    return None


def _parse_medical_record(text: str, pub_date: Optional[str] = None) -> _MedicalRecord:
    dose_m = _DOSE_RE.search(text)
    dosage = re.sub(r"\s+", "", dose_m.group(1).lower()) if dose_m else None
    route = _detect_first(text, _ROUTE_PATTERNS)
    population = _detect_first(text, _POPULATION_PATTERNS)
    endpoint = _detect_first(text, _ENDPOINT_PATTERNS)
    design = _detect_study_design(text)

    n_m = _SAMPLE_SIZE_RE.search(text)
    sample_size = int(n_m.group(1).replace(",", "")) if n_m else None
    if sample_size is not None and sample_size < 50 and design == "UNSPECIFIED":
        design = "SMALL_PILOT_STUDY"

    years = _YEAR_RE.findall(text)
    pub_year = years[-1] if years else (pub_date[:4] if pub_date and len(pub_date) >= 4 else None)

    ncts = tuple(m.group(1).upper() for m in _NCT_RE.finditer(text))
    pmids = tuple(m.group(1) for m in _PMID_RE.finditer(text))
    dois = tuple(m.group(1).lower().rstrip(".)") for m in _DOI_RE.finditer(text))
    q_cues = tuple(sorted({m.group(1).lower() for m in _BIAS_QUALITY_RE.finditer(text)}))

    low = text.lower()
    if (
        "not recommended" in low
        or "do not recommend" in low
        or "against routine screening" in low
        or re.search(r"\b(no\s+benefit|ineffective|failed\s+to\s+reduce|increased\s+mortality|contraindicated|harm)\b", low)
    ):
        pol = "NEGATIVE_INEFFECTIVE"
    elif re.search(r"\b(reduces?|prevents?|cures?|improves?|effective|lowers?|recommended|recommends|shrinks?)\b", low):
        pol = "POSITIVE_EFFECTIVE"
    else:
        pol = "NEUTRAL"

    return _MedicalRecord(
        raw_text=text,
        scope=_SixAxisClinicalScope(
            intervention=_extract_intervention(text),
            dosage=dosage,
            route=route,
            population=population,
            endpoint=endpoint,
            study_design=design,
        ),
        temporal=_TemporalContext(
            publication_year=pub_year,
            explicit_date_anchor=bool(pub_date or years),
        ),
        translational_stage=_detect_translational_stage(text),
        sample_size=sample_size,
        quality_cues=q_cues,
        nct_ids=ncts,
        pmid_ids=pmids,
        dois=dois,
        claims_causal=bool(_CAUSAL_CLAIM_RE.search(text)),
        claims_consensus=bool(_CONSENSUS_CLAIM_RE.search(text)),
        claims_peer_reviewed=bool(_PEER_REVIEW_CLAIM_RE.search(text)),
        is_guideline=bool(_GUIDELINE_ORG_RE.search(text)),
        polarity=pol,
    )


def _sentence_windows(text: str) -> List[str]:
    clean = sanitize_clinical_phi_defense_in_depth(text or "")
    protected = re.sub(
        r"\b(Dr|Mr|Mrs|Ms|Prof|vs|et\s+al|Fig|No|St|Ave|Rd)\.",
        lambda m: m.group(0).replace(".", "<DOT>"),
        clean,
        flags=re.IGNORECASE,
    )
    return [
        s.replace("<DOT>", ".").strip()
        for s in re.split(r"(?:(?<!\d)\.(?!\d)|[!?])(?:\s+|$)|\n+", protected)
        if s.strip()
    ]


def _extract_medical_claims(report_text: str) -> List[_MedicalRecord]:
    claims: List[_MedicalRecord] = []
    for raw_line in (report_text or "").splitlines():
        line = sanitize_clinical_phi_defense_in_depth(raw_line.strip().lstrip("-*").strip())
        if not line or line.startswith("#"):
            continue
        rec = _parse_medical_record(line)
        if (
            rec.scope.dosage
            or rec.scope.route
            or rec.scope.population
            or rec.scope.endpoint
            or rec.translational_stage != "UNSPECIFIED"
            or rec.claims_causal
            or rec.claims_consensus
            or rec.claims_peer_reviewed
            or rec.is_guideline
            or rec.nct_ids
            or rec.pmid_ids
        ):
            claims.append(rec)
    return claims


class MedicalVerifierPlugin:
    """Domain verifier for clinical trials, biomedical literature, guidelines, and PHI safety."""

    name = "medical_verifier"
    domain = "medical"

    def supports_category(self, category: str) -> bool:
        return (category or "").strip().lower() in {
            "medical",
            "medicine",
            "clinical",
            "health",
            "biomedical",
            "scientific",
            "science",
            "pharmacology",
            "epidemiology",
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

        # Pre-index all valid source identifiers (DOIs, PMIDs, NCTs, normalized texts) for M4 linked-erratum checks
        corpus_dois: Set[str] = set()
        corpus_pmids: Set[str] = set()
        corpus_ncts: Set[str] = set()
        non_erratum_texts: Set[str] = set()
        for sa in valid_sources:
            s_clean = sanitize_clinical_phi_defense_in_depth(sa.normalized_text)
            for m in _DOI_RE.finditer(s_clean):
                corpus_dois.add(m.group(1).lower().rstrip(".)"))
            for m in _PMID_RE.finditer(s_clean):
                corpus_pmids.add(m.group(1))
            for m in _NCT_RE.finditer(s_clean):
                corpus_ncts.add(m.group(1).upper())
            if not _ERRATUM_RE.search(s_clean):
                non_erratum_texts.add(s_clean.strip().lower())

        out: List[ClaimAssessment] = []
        for claim in _extract_medical_claims(report_text):
            supporting: List[PassageEvidenceRef] = []
            conflicting: List[PassageEvidenceRef] = []
            matched_classes: Set[str] = set()

            if claim.nct_ids and not set(claim.nct_ids).issubset(corpus_ncts):
                out.append(self._insufficient_assessment(
                    claim,
                    reason="UNRETRIEVED_CLINICAL_TRIAL_ID",
                    note=f"Cited clinical trial ID(s) {claim.nct_ids} absent from retrieved findings. {_NON_MEDICAL_ADVICE_NOTE}",
                ))
                continue
            if claim.pmid_ids and not set(claim.pmid_ids).issubset(corpus_pmids):
                out.append(self._insufficient_assessment(
                    claim,
                    reason="UNRETRIEVED_PMID_CITATION",
                    note=f"Cited PMID(s) {claim.pmid_ids} absent from retrieved findings. {_NON_MEDICAL_ADVICE_NOTE}",
                ))
                continue

            retracted_hit = False
            eoc_hit = False
            linked_erratum_confirmed = False
            unlinked_or_stale_erratum = False
            preprint_hit = False
            preprint_as_peer_reviewed = False
            observational_causal_mismatch = False
            preclinical_human_mismatch = False
            scope_mismatch_reason: Optional[str] = None
            temporal_mismatch = False
            single_pilot_consensus_mismatch = False
            guideline_conflict = False
            trial_vs_review_conflict = False
            has_explicit_date = False
            matched_designs: Set[str] = set()
            matched_polarities: Set[str] = set()
            quality_notes: List[str] = []

            for sa in valid_sources:
                src_clean = sanitize_clinical_phi_defense_in_depth(sa.normalized_text)
                sa_pub_val = getattr(getattr(sa, "publication_status", None), "value", getattr(sa, "publication_status", None))
                is_preprint_src = (
                    sa.source_class == SourceClass.PREPRINT_REPOSITORY
                    or sa_pub_val in {"PREPRINT", "PREPRINT_NOT_PEER_REVIEWED"}
                    or "medrxiv" in sa.url.lower()
                    or "biorxiv" in sa.url.lower()
                    or bool(_PREPRINT_RE.search(src_clean))
                )
                is_trusted_med_venue = (
                    sa.source_class in {
                        SourceClass.PRIMARY_INSTITUTIONAL,
                        SourceClass.PEER_REVIEWED_VENUE,
                        SourceClass.ESTABLISHED_SECONDARY,
                    }
                    or sa_pub_val in {"PEER_REVIEWED", "PEER_REVIEWED_CONFIRMED"}
                    or any(d in sa.url.lower() for d in _TRUSTED_BIOMEDICAL_DOMAINS)
                )

                for sent in _sentence_windows(src_clean):
                    s_rec = _parse_medical_record(sent, pub_date=sa.publication_date or sa.last_updated_date)

                    has_overlap = bool(
                        (claim.scope.intervention and s_rec.scope.intervention and claim.scope.intervention == s_rec.scope.intervention)
                        or (claim.nct_ids and set(claim.nct_ids) & set(s_rec.nct_ids))
                        or (claim.pmid_ids and set(claim.pmid_ids) & set(s_rec.pmid_ids))
                        or (claim.is_guideline and s_rec.is_guideline)
                        or (claim.scope.endpoint and claim.scope.endpoint == s_rec.scope.endpoint)
                        or (claim.scope.dosage and claim.scope.dosage == s_rec.scope.dosage)
                    )
                    if not has_overlap:
                        continue

                    pref = _sanitize_passage_ref(build_passage_evidence_ref(sa, query_or_claim_term=sent[:80]))
                    sc_val = sa.source_class.value if hasattr(sa.source_class, "value") else str(sa.source_class)
                    if sc_val == SourceClass.UNVERIFIED_VENUE.value and is_trusted_med_venue:
                        sc_val = SourceClass.PEER_REVIEWED_VENUE.value

                    # M4: Three-way publication integrity separation
                    if sa.retraction_warning or _RETRACTED_RE.search(sent) or _RETRACTED_RE.search(src_clean):
                        retracted_hit = True
                        conflicting.append(pref)
                        continue
                    if _EOC_RE.search(sent) or _EOC_RE.search(src_clean):
                        eoc_hit = True
                        conflicting.append(pref)
                        continue
                    if _ERRATUM_RE.search(sent) or _ERRATUM_RE.search(src_clean):
                        linked_to_original = bool(
                            (set(s_rec.dois) & corpus_dois and len(valid_sources) >= 2)
                            or (set(s_rec.pmid_ids) & corpus_pmids and len(valid_sources) >= 2)
                            or (set(s_rec.nct_ids) & corpus_ncts and len(valid_sources) >= 2)
                            or any(t and t[:30] in sent.lower() for t in non_erratum_texts)
                        )
                        dose_matches = (not claim.scope.dosage) or (claim.scope.dosage == s_rec.scope.dosage)
                        if linked_to_original and dose_matches:
                            linked_erratum_confirmed = True
                            supporting.append(pref)
                            matched_classes.add(sc_val)
                            quality_notes.append("linked_erratum_verified")
                        else:
                            unlinked_or_stale_erratum = True
                            conflicting.append(pref)
                        continue

                    # Tier 3 unverified wellness/supplement blogs cannot substantiate medical claims (M8)
                    if not is_trusted_med_venue and not is_preprint_src:
                        continue

                    # M3: Preprint detection
                    if is_preprint_src:
                        preprint_hit = True
                        if claim.claims_peer_reviewed:
                            preprint_as_peer_reviewed = True
                            conflicting.append(pref)
                            continue

                    # M5: Human clinical trial vs. animal / in-vitro translational isolation
                    if claim.translational_stage == "HUMAN_CLINICAL" and s_rec.translational_stage in {"ANIMAL_PRECLINICAL", "IN_VITRO"}:
                        preclinical_human_mismatch = True
                        conflicting.append(pref)
                        continue

                    # M1: Observational study cannot substantiate causal/curative claims
                    if claim.claims_causal and s_rec.scope.study_design == "OBSERVATIONAL_STUDY":
                        observational_causal_mismatch = True
                        conflicting.append(pref)
                        continue

                    # M2: Single small pilot study cannot substantiate "established medical consensus"
                    if claim.claims_consensus and s_rec.scope.study_design == "SMALL_PILOT_STUDY":
                        single_pilot_consensus_mismatch = True
                        conflicting.append(pref)
                        continue

                    # M6: Six-axis clinical scope isolation (dosage, route, population, endpoint evaluated independently & jointly)
                    if claim.scope.dosage and s_rec.scope.dosage and claim.scope.dosage != s_rec.scope.dosage:
                        scope_mismatch_reason = "DOSAGE_MISMATCH"
                        conflicting.append(pref)
                        continue
                    if claim.scope.route and s_rec.scope.route and claim.scope.route != s_rec.scope.route:
                        scope_mismatch_reason = "ROUTE_MISMATCH"
                        conflicting.append(pref)
                        continue
                    if claim.scope.population and s_rec.scope.population and claim.scope.population != s_rec.scope.population:
                        scope_mismatch_reason = "POPULATION_MISMATCH"
                        conflicting.append(pref)
                        continue
                    if claim.scope.endpoint and s_rec.scope.endpoint and claim.scope.endpoint != s_rec.scope.endpoint:
                        scope_mismatch_reason = "SURROGATE_VS_CLINICAL_ENDPOINT_MISMATCH"
                        conflicting.append(pref)
                        continue

                    # Separate temporal axis check (never mixed into the 6 clinical scope dimensions)
                    if claim.temporal.publication_year and s_rec.temporal.publication_year and claim.temporal.publication_year != s_rec.temporal.publication_year:
                        temporal_mismatch = True
                        conflicting.append(pref)
                        continue

                    if s_rec.temporal.explicit_date_anchor:
                        has_explicit_date = True
                    matched_designs.add(s_rec.scope.study_design)
                    if s_rec.polarity != "NEUTRAL":
                        matched_polarities.add(s_rec.polarity)
                    if s_rec.sample_size is not None:
                        quality_notes.append(f"n={s_rec.sample_size}")
                    quality_notes.extend(s_rec.quality_cues)

                    if claim.polarity != "NEUTRAL" and s_rec.polarity != "NEUTRAL" and claim.polarity != s_rec.polarity:
                        conflicting.append(pref)
                    else:
                        supporting.append(pref)
                    matched_classes.add(sc_val)

            if len(matched_polarities) >= 2:
                if claim.is_guideline:
                    guideline_conflict = True
                elif "SYSTEMATIC_REVIEW_META_ANALYSIS" in matched_designs and "RANDOMIZED_CONTROLLED_TRIAL" in matched_designs:
                    trial_vs_review_conflict = True

            if (
                retracted_hit
                or eoc_hit
                or unlinked_or_stale_erratum
                or preprint_as_peer_reviewed
                or observational_causal_mismatch
                or preclinical_human_mismatch
                or scope_mismatch_reason is not None
                or temporal_mismatch
                or single_pilot_consensus_mismatch
                or guideline_conflict
                or trial_vs_review_conflict
                or len(matched_polarities) >= 2
                or conflicting
            ):
                if retracted_hit:
                    pub_st = "RETRACTED"
                    ev_cls = "RETRACTED_SOURCE_EXCLUDED"
                elif eoc_hit:
                    pub_st = "EXPRESSION_OF_CONCERN"
                    ev_cls = "EXPRESSION_OF_CONCERN_FLAGGED"
                elif unlinked_or_stale_erratum:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "UNLINKED_OR_STALE_ERRATUM"
                elif preprint_as_peer_reviewed:
                    pub_st = "PREPRINT_NOT_PEER_REVIEWED"
                    ev_cls = "PREPRINT_PRESENTED_AS_PEER_REVIEWED"
                elif observational_causal_mismatch:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "CAUSAL_CLAIM_FROM_OBSERVATIONAL_STUDY"
                elif preclinical_human_mismatch:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "PRECLINICAL_TRANSLATIONAL_MISMATCH"
                elif scope_mismatch_reason:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = scope_mismatch_reason
                elif temporal_mismatch:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "TEMPORAL_VERSION_MISMATCH"
                elif single_pilot_consensus_mismatch:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "SINGLE_STUDY_NOT_SYSTEMATIC_CONSENSUS"
                elif guideline_conflict:
                    pub_st = "NOT_APPLICABLE"
                    ev_cls = "CONFLICTING_CLINICAL_GUIDELINES"
                elif trial_vs_review_conflict:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "CLINICAL_TRIAL_VS_SYSTEMATIC_REVIEW_CONFLICT"
                else:
                    pub_st = "PEER_REVIEWED"
                    ev_cls = "CONFLICTING_CLINICAL_EVIDENCE"

                out.append(ClaimAssessment(
                    claim_text=claim.raw_text,
                    domain=self.domain,
                    plugin_name=self.name,
                    claim_assessment="CONTESTED",
                    publication_status=pub_st,
                    legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
                    source_dependence=DependenceConfidence.UNKNOWN.value,
                    evidence_classification=ev_cls,
                    authority_scope_note=(
                        f"Methodological, scope, integrity, or guideline conflict ({ev_cls}); "
                        f"quality_cues={sorted(set(quality_notes)) or ['unspecified']}. {_NON_MEDICAL_ADVICE_NOTE}"
                    ),
                    supporting_passages=supporting,
                    conflicting_passages=conflicting,
                    rationale={
                        "source_authority": ", ".join(sorted(matched_classes)) or "CONFLICTING_BIOMEDICAL_SOURCES",
                        "independence": "Competing biomedical authorities preserved under PRESERVE_CONFLICT_NO_CONSENSUS",
                        "directness": ev_cls,
                        "contradictions": f"Conflict or scope/integrity boundary triggered: {ev_cls}",
                        "evidence_completeness": "No live PubMed/FDA MedWatch/Retraction Watch feed; preserves evidence limits",
                    },
                    verifier_version=MEDICAL_VERIFIER_VERSION,
                ))
                continue

            if not supporting:
                out.append(self._insufficient_assessment(
                    claim,
                    reason="NO_AUTHORITATIVE_CLINICAL_EVIDENCE",
                    note=f"No peer-reviewed, regulatory, or guideline authority substantiates this clinical scope. {_NON_MEDICAL_ADVICE_NOTE}",
                ))
                continue

            if preprint_hit:
                pub_status = "PREPRINT_NOT_PEER_REVIEWED"
            elif linked_erratum_confirmed:
                pub_status = "PEER_REVIEWED_CORRECTED"
            elif SourceClass.PEER_REVIEWED_VENUE.value in matched_classes:
                pub_status = "PEER_REVIEWED"
            else:
                pub_status = "NOT_APPLICABLE"

            currency_val = (
                LegalCurrencyStatus.CURRENT_VERIFIED.value
                if has_explicit_date
                else LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value
            )

            matched_urls = {p.source_url for p in supporting}
            matched_indep = matched_urls & indep_urls
            has_tier1 = bool(matched_classes & {SourceClass.PRIMARY_INSTITUTIONAL.value, SourceClass.PEER_REVIEWED_VENUE.value})
            if has_tier1 and (len(matched_indep) >= 2 or len(matched_urls) >= 2) and not preprint_hit:
                ev_cls = "PRIMARY_CORROBORATED"
            elif preprint_hit:
                ev_cls = "PREPRINT_UNREVIEWED_EVIDENCE"
            else:
                ev_cls = "SINGLE_SOURCE_CLINICAL_EVIDENCE"

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
                publication_status=pub_status,
                legal_currency=currency_val,
                source_dependence=dep_conf,
                evidence_classification=ev_cls,
                authority_scope_note=(
                    f"Six-axis clinical scope bound (design={claim.scope.study_design}, "
                    f"stage={claim.translational_stage}, quality={sorted(set(quality_notes)) or ['unspecified']}). "
                    f"{_NON_MEDICAL_ADVICE_NOTE}"
                ),
                supporting_passages=supporting,
                rationale={
                    "source_authority": ", ".join(sorted(matched_classes)),
                    "independence": f"{len(matched_indep)} verified independent origin(s)",
                    "directness": "SIX_AXIS_CLINICAL_SCOPE_BOUND",
                    "contradictions": "None detected within retrieved biomedical corpus",
                    "evidence_completeness": "Verified against retrieved literature only; not medical advice",
                },
                verifier_version=MEDICAL_VERIFIER_VERSION,
            ))

        return out

    def _insufficient_assessment(self, claim: _MedicalRecord, *, reason: str, note: str) -> ClaimAssessment:
        return ClaimAssessment(
            claim_text=claim.raw_text,
            domain=self.domain,
            plugin_name=self.name,
            claim_assessment="INSUFFICIENT_EVIDENCE",
            publication_status="NOT_APPLICABLE",
            legal_currency=LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED.value,
            source_dependence=DependenceConfidence.UNKNOWN.value,
            evidence_classification=reason,
            authority_scope_note=note,
            supporting_passages=[],
            rationale={
                "source_authority": "No Tier 1/Tier 2 clinical authority matching 6-axis scope",
                "independence": "UNKNOWN or unverified source only (fail-closed)",
                "directness": reason,
                "contradictions": "None detected",
                "evidence_completeness": "Fail-closed; missing evidence never becomes clinical certainty",
            },
            verifier_version=MEDICAL_VERIFIER_VERSION,
        )
