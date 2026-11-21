"""DR-5.1 / DR-5.1.1 Standalone Source Authority, Epistemic Metadata & Typed Provenance Graph.

Implements the DR-5 v3.1 source assessment architecture without modifying or
replacing the accepted DR-4.2 baseline verifier:
  - Fail-closed SourceClass classification (defaults to UNVERIFIED_VENUE).
  - PublicationType & PublicationStatus decoupling (DOI or journal title alone
    never establishes peer review; defaults to PEER_REVIEW_STATUS_UNVERIFIED).
  - LegalCurrencyStatus (defaults to CURRENT_STATUS_NOT_ESTABLISHED unless
    current enforceability is explicitly verified).
  - MedicalEvidenceTier hierarchy tagging.
  - Typed, anti-transitive Provenance Graph with DependenceConfidence levels;
    UNKNOWN provenance never counts as independent corroboration; independent
    measurements are assessed per pairwise evidence relationship.
  - Immediate pre-storage PII/credential sanitization (synthetic JWTs, PEM keys,
    contextual routing/account numbers, OCR-corrupted tokens) and exact
    sanitized-text offset reproducibility (sanitized_text_sha256).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

EXTRACTION_VERSION = "extract-v1.0"
NORMALIZATION_VERSION = "norm-v1.0"
VERIFIER_VERSION = "dr5.1.1-v3.1"


class SourceClass(str, Enum):
    PRIMARY_INSTITUTIONAL = "PRIMARY_INSTITUTIONAL"
    PEER_REVIEWED_VENUE = "PEER_REVIEWED_VENUE"
    INDEX_OR_RESOLVER = "INDEX_OR_RESOLVER"
    PREPRINT_REPOSITORY = "PREPRINT_REPOSITORY"
    ESTABLISHED_SECONDARY = "ESTABLISHED_SECONDARY"
    UNVERIFIED_VENUE = "UNVERIFIED_VENUE"
    UNVERIFIED_CITATION = "UNVERIFIED_CITATION"


class PublicationType(str, Enum):
    CLINICAL_GUIDELINE = "CLINICAL_GUIDELINE"
    SYSTEMATIC_REVIEW = "SYSTEMATIC_REVIEW"
    RESEARCH_ARTICLE = "RESEARCH_ARTICLE"
    EDITORIAL_OR_LETTER = "EDITORIAL_OR_LETTER"
    CONFERENCE_ABSTRACT = "CONFERENCE_ABSTRACT"
    PREPRINT = "PREPRINT"
    OFFICIAL_FILING_OR_STATUTE = "OFFICIAL_FILING_OR_STATUTE"
    TECHNICAL_SPEC_OR_DOCS = "TECHNICAL_SPEC_OR_DOCS"
    NEWS_OR_ANALYSIS = "NEWS_OR_ANALYSIS"
    UNSPECIFIED = "UNSPECIFIED"


class PublicationStatus(str, Enum):
    PEER_REVIEWED_VERIFIED = "PEER_REVIEWED_VERIFIED"
    PEER_REVIEW_STATUS_UNVERIFIED = "PEER_REVIEW_STATUS_UNVERIFIED"
    PREPRINT_UNREVIEWED = "PREPRINT_UNREVIEWED"
    EDITORIAL_NON_RESEARCH = "EDITORIAL_NON_RESEARCH"
    RETRACTED_OR_WITHDRAWN = "RETRACTED_OR_WITHDRAWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LegalCurrencyStatus(str, Enum):
    CURRENT_VERIFIED = "CURRENT_VERIFIED"
    CURRENT_STATUS_NOT_ESTABLISHED = "CURRENT_STATUS_NOT_ESTABLISHED"
    AMENDED_OR_SUPERSEDED = "AMENDED_OR_SUPERSEDED"
    OVERRULED_OR_VACATED = "OVERRULED_OR_VACATED"
    PROPOSED_OR_NOT_IN_FORCE = "PROPOSED_OR_NOT_IN_FORCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MedicalEvidenceTier(str, Enum):
    CLINICAL_PRACTICE_GUIDELINE = "CLINICAL_PRACTICE_GUIDELINE"
    SYSTEMATIC_REVIEW_META_ANALYSIS = "SYSTEMATIC_REVIEW_META_ANALYSIS"
    RANDOMIZED_CONTROLLED_TRIAL = "RANDOMIZED_CONTROLLED_TRIAL"
    OBSERVATIONAL_COHORT = "OBSERVATIONAL_COHORT"
    CASE_REPORT_OR_PRECLINICAL = "CASE_REPORT_OR_PRECLINICAL"
    UNREVIEWED_PREPRINT = "UNREVIEWED_PREPRINT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProvenanceRelation(str, Enum):
    SYNDICATED_FROM = "SYNDICATED_FROM"
    DERIVED_FROM = "DERIVED_FROM"
    SHARES_PRIMARY_SOURCE = "SHARES_PRIMARY_SOURCE"
    CITES_STUDY = "CITES_STUDY"
    SAME_PUBLISHER = "SAME_PUBLISHER"
    INDEPENDENT_MEASUREMENT = "INDEPENDENT_MEASUREMENT"


class DependenceConfidence(str, Enum):
    CONFIRMED_DEPENDENT = "CONFIRMED_DEPENDENT"
    LIKELY_DEPENDENT = "LIKELY_DEPENDENT"
    POSSIBLY_RELATED = "POSSIBLY_RELATED"
    INDEPENDENT = "INDEPENDENT"
    UNKNOWN = "UNKNOWN"


_COLLAPSING_RELATIONS = {
    ProvenanceRelation.SYNDICATED_FROM,
    ProvenanceRelation.DERIVED_FROM,
    ProvenanceRelation.SHARES_PRIMARY_SOURCE,
}
_COLLAPSING_CONFIDENCES = {
    DependenceConfidence.CONFIRMED_DEPENDENT,
    DependenceConfidence.LIKELY_DEPENDENT,
}

_PREPRINT_DOMAINS = {"arxiv.org", "medrxiv.org", "biorxiv.org", "ssrn.com", "researchsquare.com"}
_RESOLVER_INDEX_DOMAINS = {"doi.org", "dx.doi.org", "pubmed.ncbi.nlm.nih.gov", "semanticscholar.org"}
_ESTABLISHED_SECONDARY_DOMAINS = {
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "anandtech.com", "techpowerup.com", "notebookcheck.net", "tomshardware.com", "gamersnexus.net",
}
_OFFICIAL_VENDOR_DOCS = {"nvidia.com", "amd.com", "intel.com", "apple.com", "docs.python.org", "sqlite.org"}

# Contextual and OCR-tolerant sensitive data redaction patterns (never indiscriminately strips plain numeric counts).
_PII_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\s*\.\s*eyJ[A-Za-z0-9_-]{6,}\s*\.\s*[A-Za-z0-9_-]{6,}\b"), "[REDACTED_JWT]"),
    (re.compile(r"\b(?:Bearer\s+eyJ[A-Za-z0-9_\s.-]{12,}|Bearer\s+[A-Za-z0-9._-]{16,}|(?:sk|ghp|AIza)[-_\s]?[A-Za-z0-9_-]{16,})\b"), "[REDACTED_CREDENTIAL]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"(?i)\b(?:aba\s+|bank\s+)?routing\s*(?:number|num|no\.?|#|:)?\s*[:#-]?\s*\d{9}\b"), "[REDACTED_ROUTING]"),
    (re.compile(r"(?i)\b(?:bank\s+|checking\s+|savings\s+)?account\s*(?:number|num|no\.?|#|:)\s*[:#-]?\s*\d{6,17}\b"), "[REDACTED_ACCOUNT]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
]

_WIRE_BYLINE_RE = re.compile(r"\b(?:associated press|reuters|pr newswire|business wire|globenewswire|originally appeared on|republished from)\b", re.IGNORECASE)
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", re.IGNORECASE)
_NCT_RE = re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)
_SEC_FILING_RE = re.compile(r"\b(?:Form\s+10-[KQ]|Form\s+8-K|SEC\s+filing)\b", re.IGNORECASE)
_HARDWARE_METHODOLOGY_RE = re.compile(
    r"\b(?:test\s+(?:bench|rig|system|methodology|setup)|benchmarked\s+(?:on|using)|power\s+(?:analyzer|meter)|frame\s+time|ambient\s+temperature|driver\s+version)\b",
    re.IGNORECASE,
)
_FINANCIAL_METHODOLOGY_RE = re.compile(
    r"\b(?:discounted\s+cash\s+flow|dcf\s+model|independently\s+calculated|regression\s+analysis|duration\s+sensitivity\s+model|custom\s+valuation)\b",
    re.IGNORECASE,
)
_PUB_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2})\b", re.IGNORECASE)
_UPDATED_DATE_RE = re.compile(r"(?:updated|modified|revised)\s*:?\s*(20\d{2}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+20\d{2})", re.IGNORECASE)


def normalize_extracted_text(text: str) -> str:
    """Deterministic text normalization matching NORMALIZATION_VERSION."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def sanitize_text_full(text: str) -> str:
    """Normalize and sanitize credentials/PII before any intermediate storage, logging, or slicing."""
    cleaned = normalize_extracted_text(text)
    for pat, replacement in _PII_PATTERNS:
        cleaned = pat.sub(replacement, cleaned)
    return cleaned


def sanitize_excerpt(text: str, max_len: int = 320) -> str:
    """Redact credentials and PII and bound excerpt length."""
    cleaned = sanitize_text_full(text)
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip() + "..."
    return cleaned


def _registrable_domain(url: str) -> str:
    host = (urlparse(str(url or "")).hostname or "").lower().strip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "ac", "gov", "org", "edu"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _shingles(text: str, k: int = 5) -> Set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ProvenanceEdge:
    source_url: str
    target_url: str
    relation: ProvenanceRelation
    confidence: DependenceConfidence
    rationale: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "source_url": self.source_url,
            "target_url": self.target_url,
            "relation": self.relation.value,
            "confidence": self.confidence.value,
            "rationale": self.rationale,
        }


@dataclass
class PassageEvidenceRef:
    source_url: str
    retrieval_timestamp: str
    publication_date: Optional[str]
    last_updated_date: Optional[str]
    content_sha256: str
    sanitized_text_sha256: str
    extraction_version: str
    normalization_version: str
    char_start: int
    char_end: int
    excerpt: str
    source_class: str
    publication_type: str
    publication_status: str
    legal_currency: str
    owner_scope: str = "public_web"
    sensitivity_tier: str = "public"
    provenance_edges: List[Dict[str, str]] = field(default_factory=list)
    verifier_version: str = VERIFIER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SourceAssessment:
    url: str
    registrable_domain: str
    source_class: SourceClass
    publication_type: PublicationType
    publication_status: PublicationStatus
    legal_currency: LegalCurrencyStatus
    medical_tier: MedicalEvidenceTier
    retraction_warning: bool
    has_independent_methodology: bool
    publication_date: Optional[str]
    last_updated_date: Optional[str]
    retrieval_timestamp: str
    content_sha256: str
    sanitized_text_sha256: str
    normalized_text: str
    authority_scope_note: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source_class"] = self.source_class.value
        d["publication_type"] = self.publication_type.value
        d["publication_status"] = self.publication_status.value
        d["legal_currency"] = self.legal_currency.value
        d["medical_tier"] = self.medical_tier.value
        return d


def reconstruct_sanitized_text(assessment: SourceAssessment) -> str:
    """Return the exact sanitized, normalized text indexed by PassageEvidenceRef offsets."""
    return sanitize_text_full(assessment.normalized_text)


def assess_source_authority(
    finding: Dict[str, Any],
    *,
    is_verified_fetched_url: bool = True,
    retrieval_timestamp: Optional[str] = None,
) -> SourceAssessment:
    """Classify a source with immediate pre-storage sanitization and fail-closed metadata handling."""
    url = str(finding.get("url") or finding.get("source_url") or "").strip()
    title = str(finding.get("title") or "").strip()
    raw_body = " ".join(str(finding.get(k) or "") for k in ("summary", "content", "snippet", "text", "excerpt")).strip()
    combined = f"{title} {raw_body}".strip()
    raw_norm = normalize_extracted_text(combined)
    raw_content_hash = hashlib.sha256(raw_norm.encode("utf-8")).hexdigest()

    # Safeguard A: Sanitize immediately so raw sensitive strings are NEVER retained on SourceAssessment.
    sanitized_norm = sanitize_text_full(raw_norm)
    sanitized_hash = hashlib.sha256(sanitized_norm.encode("utf-8")).hexdigest()

    ret_ts = retrieval_timestamp or datetime.now(timezone.utc).isoformat()
    domain = _registrable_domain(url)
    host = (urlparse(url).hostname or "").lower()

    pub_match = _PUB_DATE_RE.search(sanitized_norm)
    upd_match = _UPDATED_DATE_RE.search(sanitized_norm)
    pub_date = pub_match.group(1) if pub_match else None
    upd_date = upd_match.group(1) if upd_match else None

    retraction_warning = bool(re.search(r"\b(?:retracted|retraction notice|withdrawn by author)\b", sanitized_norm, re.IGNORECASE))

    if not is_verified_fetched_url or not url:
        return SourceAssessment(
            url=url,
            registrable_domain=domain,
            source_class=SourceClass.UNVERIFIED_CITATION,
            publication_type=PublicationType.UNSPECIFIED,
            publication_status=PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED,
            legal_currency=LegalCurrencyStatus.NOT_APPLICABLE,
            medical_tier=MedicalEvidenceTier.NOT_APPLICABLE,
            retraction_warning=retraction_warning,
            has_independent_methodology=False,
            publication_date=pub_date,
            last_updated_date=upd_date,
            retrieval_timestamp=ret_ts,
            content_sha256=raw_content_hash,
            sanitized_text_sha256=sanitized_hash,
            normalized_text=sanitized_norm,
            authority_scope_note="Unverified citation URL not present in retrieved source set.",
        )

    source_class = SourceClass.UNVERIFIED_VENUE
    pub_type = PublicationType.UNSPECIFIED
    pub_status = PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED
    legal_currency = LegalCurrencyStatus.NOT_APPLICABLE
    medical_tier = MedicalEvidenceTier.NOT_APPLICABLE
    scope_note = "Insufficient venue/publisher metadata; treated as unverified venue."

    is_gov_or_mil = host.endswith(".gov") or host.endswith(".mil") or domain in {"congress.gov", "supremecourt.gov", "sec.gov", "federalregister.gov"}
    is_official_docs = domain in _OFFICIAL_VENDOR_DOCS or any(host.endswith(f".{d}") for d in _OFFICIAL_VENDOR_DOCS)

    if is_gov_or_mil:
        source_class = SourceClass.PRIMARY_INSTITUTIONAL
        pub_type = PublicationType.OFFICIAL_FILING_OR_STATUTE
        pub_status = PublicationStatus.NOT_APPLICABLE
        scope_note = "Official government/institutional issuer; authoritative for its own text/statistics, not third-party interpretations."
    elif is_official_docs:
        source_class = SourceClass.PRIMARY_INSTITUTIONAL
        pub_type = PublicationType.TECHNICAL_SPEC_OR_DOCS
        pub_status = PublicationStatus.NOT_APPLICABLE
        scope_note = "Official vendor/documentation issuer; authoritative for own product specs, not comparative competitor claims."
    elif domain in _PREPRINT_DOMAINS or re.search(r"\b(?:preprint|not peer[- ]reviewed)\b", sanitized_norm, re.IGNORECASE):
        source_class = SourceClass.PREPRINT_REPOSITORY
        pub_type = PublicationType.PREPRINT
        pub_status = PublicationStatus.PREPRINT_UNREVIEWED
        medical_tier = MedicalEvidenceTier.UNREVIEWED_PREPRINT
        scope_note = "Unreviewed preprint repository; provisional evidence only."
    elif domain in _RESOLVER_INDEX_DOMAINS:
        source_class = SourceClass.INDEX_OR_RESOLVER
        pub_type = PublicationType.UNSPECIFIED
        pub_status = PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED
        scope_note = "Identifier resolver or bibliographic index; peer-review status unverified without full publication metadata."
    elif domain in _ESTABLISHED_SECONDARY_DOMAINS:
        source_class = SourceClass.ESTABLISHED_SECONDARY
        pub_type = PublicationType.NEWS_OR_ANALYSIS
        pub_status = PublicationStatus.NOT_APPLICABLE
        scope_note = "Established secondary publication; independent measurement requires explicit methodology."

    if re.search(r"\b(?:editorial|letter to the editor|commentary|correspondence)\b", sanitized_norm, re.IGNORECASE):
        pub_type = PublicationType.EDITORIAL_OR_LETTER
        pub_status = PublicationStatus.EDITORIAL_NON_RESEARCH
    elif re.search(r"\b(?:conference abstract|poster abstract|meeting abstract)\b", sanitized_norm, re.IGNORECASE):
        pub_type = PublicationType.CONFERENCE_ABSTRACT
        pub_status = PublicationStatus.PEER_REVIEW_STATUS_UNVERIFIED
    elif (
        source_class not in {SourceClass.PREPRINT_REPOSITORY, SourceClass.INDEX_OR_RESOLVER}
        and re.search(r"\bpeer[- ]reviewed\b", sanitized_norm, re.IGNORECASE)
        and re.search(r"\b(?:journal|vol\.|volume|issue)\b", sanitized_norm, re.IGNORECASE)
    ):
        source_class = SourceClass.PEER_REVIEWED_VENUE
        pub_type = PublicationType.RESEARCH_ARTICLE
        pub_status = PublicationStatus.PEER_REVIEWED_VERIFIED
        scope_note = "Peer-reviewed journal article metadata verified in extracted source."

    if retraction_warning:
        pub_status = PublicationStatus.RETRACTED_OR_WITHDRAWN

    if re.search(r"\bclinical practice guideline\b", sanitized_norm, re.IGNORECASE):
        medical_tier = MedicalEvidenceTier.CLINICAL_PRACTICE_GUIDELINE
        if pub_type == PublicationType.UNSPECIFIED:
            pub_type = PublicationType.CLINICAL_GUIDELINE
    elif re.search(r"\b(?:systematic review|meta-analysis)\b", sanitized_norm, re.IGNORECASE):
        medical_tier = MedicalEvidenceTier.SYSTEMATIC_REVIEW_META_ANALYSIS
        if pub_type == PublicationType.UNSPECIFIED:
            pub_type = PublicationType.SYSTEMATIC_REVIEW
    elif re.search(r"\b(?:randomized controlled trial|double-blind placebo|NCT\d{8})\b", sanitized_norm, re.IGNORECASE):
        medical_tier = MedicalEvidenceTier.RANDOMIZED_CONTROLLED_TRIAL
    elif re.search(r"\b(?:cohort study|observational study|case-control)\b", sanitized_norm, re.IGNORECASE):
        medical_tier = MedicalEvidenceTier.OBSERVATIONAL_COHORT

    if re.search(r"(?:\b\d+\s+U\.S\.C\.|\b\d+\s+C\.F\.R\.|\bstatute\b|\bregulation\b|\bsection\s+\d+)", sanitized_norm, re.IGNORECASE):
        if re.search(r"\b(?:repealed|superseded|amended by)\b", sanitized_norm, re.IGNORECASE):
            legal_currency = LegalCurrencyStatus.AMENDED_OR_SUPERSEDED
        elif re.search(r"\b(?:overruled|vacated|enjoined)\b", sanitized_norm, re.IGNORECASE):
            legal_currency = LegalCurrencyStatus.OVERRULED_OR_VACATED
        elif re.search(r"\b(?:proposed rule|draft legislation|not yet effective)\b", sanitized_norm, re.IGNORECASE):
            legal_currency = LegalCurrencyStatus.PROPOSED_OR_NOT_IN_FORCE
        elif is_gov_or_mil and re.search(r"\b(?:current through|in force as of|codified at)\b", sanitized_norm, re.IGNORECASE):
            legal_currency = LegalCurrencyStatus.CURRENT_VERIFIED
        else:
            legal_currency = LegalCurrencyStatus.CURRENT_STATUS_NOT_ESTABLISHED

    has_indep_method = bool(
        _HARDWARE_METHODOLOGY_RE.search(sanitized_norm) or _FINANCIAL_METHODOLOGY_RE.search(sanitized_norm)
    )

    return SourceAssessment(
        url=url,
        registrable_domain=domain,
        source_class=source_class,
        publication_type=pub_type,
        publication_status=pub_status,
        legal_currency=legal_currency,
        medical_tier=medical_tier,
        retraction_warning=retraction_warning,
        has_independent_methodology=has_indep_method,
        publication_date=pub_date,
        last_updated_date=upd_date,
        retrieval_timestamp=ret_ts,
        content_sha256=raw_content_hash,
        sanitized_text_sha256=sanitized_hash,
        normalized_text=sanitized_norm,
        authority_scope_note=scope_note,
    )


def build_provenance_graph(assessments: List[SourceAssessment]) -> Tuple[List[ProvenanceEdge], Dict[str, Any]]:
    """Construct a typed, anti-transitive provenance graph assessing independence per pairwise evidence relationship."""
    edges: List[ProvenanceEdge] = []
    # Deduplicate input assessments by URL deterministically while preserving order
    unique_assessments: List[SourceAssessment] = []
    seen_urls: Set[str] = set()
    for item in assessments:
        if item.url and item.url not in seen_urls:
            seen_urls.add(item.url)
            unique_assessments.append(item)

    shingle_cache = {a.url: _shingles(a.normalized_text, k=5) for a in unique_assessments}

    for i in range(len(unique_assessments)):
        for j in range(i + 1, len(unique_assessments)):
            a, b = unique_assessments[i], unique_assessments[j]
            jac = _jaccard(shingle_cache[a.url], shingle_cache[b.url])
            dois_a, dois_b = set(_DOI_RE.findall(a.normalized_text)), set(_DOI_RE.findall(b.normalized_text))
            ncts_a, ncts_b = set(_NCT_RE.findall(a.normalized_text)), set(_NCT_RE.findall(b.normalized_text))
            sec_a, sec_b = bool(_SEC_FILING_RE.search(a.normalized_text)), bool(_SEC_FILING_RE.search(b.normalized_text))

            if jac >= 0.60 or (_WIRE_BYLINE_RE.search(a.normalized_text) and _WIRE_BYLINE_RE.search(b.normalized_text) and jac >= 0.35):
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.SYNDICATED_FROM,
                    confidence=DependenceConfidence.CONFIRMED_DEPENDENT,
                    rationale=f"High textual/wire syndication overlap (5-gram Jaccard={jac:.2f}).",
                ))
            elif (
                (a.source_class == SourceClass.PRIMARY_INSTITUTIONAL and b.source_class != SourceClass.PRIMARY_INSTITUTIONAL and not b.has_independent_methodology and jac >= 0.20)
                or (b.source_class == SourceClass.PRIMARY_INSTITUTIONAL and a.source_class != SourceClass.PRIMARY_INSTITUTIONAL and not a.has_independent_methodology and jac >= 0.20)
            ):
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.DERIVED_FROM,
                    confidence=DependenceConfidence.LIKELY_DEPENDENT,
                    rationale="Secondary summary restating primary issuer announcement without independent methodology.",
                ))
            elif (sec_a and sec_b and not a.has_independent_methodology and not b.has_independent_methodology) or (
                not a.has_independent_methodology and not b.has_independent_methodology and jac >= 0.30
            ):
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.SHARES_PRIMARY_SOURCE,
                    confidence=DependenceConfidence.LIKELY_DEPENDENT,
                    rationale="Multiple secondary reports summarizing the same underlying announcement/filing.",
                ))
            elif (dois_a & dois_b) or (ncts_a & ncts_b):
                shared = sorted((dois_a & dois_b) | (ncts_a & ncts_b))
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.CITES_STUDY,
                    confidence=DependenceConfidence.POSSIBLY_RELATED,
                    rationale=f"References shared study identifier(s): {', '.join(shared)}.",
                ))
            elif a.registrable_domain and a.registrable_domain == b.registrable_domain:
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.SAME_PUBLISHER,
                    confidence=DependenceConfidence.POSSIBLY_RELATED,
                    rationale=f"Same registrable domain ({a.registrable_domain}); separate articles not auto-collapsed.",
                ))
            elif (
                a.registrable_domain != b.registrable_domain
                and (a.has_independent_methodology or a.source_class == SourceClass.PRIMARY_INSTITUTIONAL)
                and (b.has_independent_methodology or b.source_class == SourceClass.PRIMARY_INSTITUTIONAL)
                and (a.has_independent_methodology or b.has_independent_methodology)
            ):
                edges.append(ProvenanceEdge(
                    source_url=a.url,
                    target_url=b.url,
                    relation=ProvenanceRelation.INDEPENDENT_MEASUREMENT,
                    confidence=DependenceConfidence.INDEPENDENT,
                    rationale="Pairwise evidence relationship shows distinct origin with documented independent methodology.",
                ))

    collapsed_into: Dict[str, str] = {}
    for edge in edges:
        if edge.relation in _COLLAPSING_RELATIONS and edge.confidence in _COLLAPSING_CONFIDENCES:
            if edge.target_url not in collapsed_into and edge.source_url not in collapsed_into:
                collapsed_into[edge.target_url] = edge.source_url

    verified_independent_origins: List[str] = []
    unverified_independence_urls: List[str] = []
    for a in unique_assessments:
        if a.source_class == SourceClass.UNVERIFIED_CITATION:
            continue
        if a.url in collapsed_into:
            continue
        if a.source_class == SourceClass.PRIMARY_INSTITUTIONAL or a.has_independent_methodology:
            verified_independent_origins.append(a.url)
        else:
            unverified_independence_urls.append(a.url)

    independent_measurement_pairs = [
        (e.source_url, e.target_url)
        for e in edges
        if e.relation == ProvenanceRelation.INDEPENDENT_MEASUREMENT and e.confidence == DependenceConfidence.INDEPENDENT
    ]

    summary = {
        "total_sources": len(unique_assessments),
        "verified_independent_origin_count": len(verified_independent_origins),
        "verified_independent_origins": verified_independent_origins,
        "independent_measurement_pair_count": len(independent_measurement_pairs),
        "unverified_independence_count": len(unverified_independence_urls),
        "unverified_independence_urls": unverified_independence_urls,
        "collapsed_duplicate_count": len(collapsed_into),
    }
    return edges, summary


def build_passage_evidence_ref(
    assessment: SourceAssessment,
    *,
    query_or_claim_term: str,
    edges: Optional[List[ProvenanceEdge]] = None,
    owner_scope: str = "public_web",
    sensitivity_tier: str = "public",
) -> PassageEvidenceRef:
    """Create a reproducible PassageEvidenceRef whose offsets index directly into reconstruct_sanitized_text(assessment)."""
    sanitized_full = reconstruct_sanitized_text(assessment)
    idx = sanitized_full.lower().find(query_or_claim_term.lower()) if query_or_claim_term else -1
    if idx >= 0:
        start = max(0, idx - 120)
        end = min(len(sanitized_full), idx + len(query_or_claim_term) + 140)
    else:
        start = 0
        end = min(len(sanitized_full), 280)

    excerpt = sanitized_full[start:end]
    rel_edges = [
        e.to_dict()
        for e in (edges or [])
        if e.source_url == assessment.url or e.target_url == assessment.url
    ]

    return PassageEvidenceRef(
        source_url=assessment.url,
        retrieval_timestamp=assessment.retrieval_timestamp,
        publication_date=assessment.publication_date,
        last_updated_date=assessment.last_updated_date,
        content_sha256=assessment.content_sha256,
        sanitized_text_sha256=assessment.sanitized_text_sha256,
        extraction_version=EXTRACTION_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        char_start=start,
        char_end=end,
        excerpt=excerpt,
        source_class=assessment.source_class.value,
        publication_type=assessment.publication_type.value,
        publication_status=assessment.publication_status.value,
        legal_currency=assessment.legal_currency.value,
        owner_scope=owner_scope,
        sensitivity_tier=sensitivity_tier,
        provenance_edges=rel_edges,
        verifier_version=VERIFIER_VERSION,
    )
