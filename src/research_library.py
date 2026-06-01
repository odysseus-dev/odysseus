"""Library-document retrieval for Deep Research.

Deep Research is web-first, but a user's own Library documents are often the
most authoritative source for a question. Reading *every* document for every
research run would be slow and expensive, so this module retrieves only the
handful that are actually relevant, using semantic (embedding) similarity:

  1. Load the requester's Library documents (owner-scoped — a user can only
     ever pull their OWN documents into a research run).
  2. Split each document into overlapping ~800-char chunks.
  3. Embed the research question + all chunks once (single batched call) with
     the same embedding stack the rest of the app uses (HTTP API → local
     FastEmbed fallback). Vectors are L2-normalised, so cosine similarity is a
     plain dot product.
  4. Score each document by its best-matching chunk, keep the top-K above a
     small relevance floor, and surface that best chunk as the excerpt.

If the embedding backend is unavailable, it degrades to a keyword-overlap
score so the feature still works (just less precisely). Retrieval never raises
into the research loop — any failure yields an empty list.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Bounds — keep retrieval cheap even for large libraries. Anything dropped by
# these caps is logged (no silent truncation).
_MAX_CANDIDATE_DOCS = 300      # most-recently-updated docs considered
_MAX_CHUNKS_PER_DOC = 8        # cap chunks for a single very long document
_MAX_TOTAL_CHUNKS = 600        # global cap across all candidate docs
_CHUNK_CHARS = 800
_CHUNK_OVERLAP = 120
_SUMMARY_CHARS = 600
_EVIDENCE_CHARS = 2000
_DEFAULT_MIN_SCORE = 0.15      # cosine floor; below this a doc is "not relevant"


def _chunk_text(text: str) -> List[str]:
    """Split text into overlapping windows on a best-effort word boundary."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= _CHUNK_CHARS:
        return [text]
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n and len(chunks) < _MAX_CHUNKS_PER_DOC:
        end = min(start + _CHUNK_CHARS, n)
        # Prefer to break on whitespace so we don't slice words in half.
        if end < n:
            sp = text.rfind(" ", start + int(_CHUNK_CHARS * 0.6), end)
            if sp != -1:
                end = sp
        chunks.append(text[start:end].strip())
        if end >= n:
            break
        start = max(end - _CHUNK_OVERLAP, start + 1)
    return [c for c in chunks if c]


def _load_candidate_docs(owner: str) -> List[Dict]:
    """Return [{id, title, content}] for the owner's active, non-archived docs.

    Owner-scoped to match the Library's own visibility rules — research can
    only ever read documents the requester owns.
    """
    if not owner:
        return []
    try:
        from sqlalchemy import or_
        from core.database import SessionLocal, Document
    except Exception as e:  # pragma: no cover - import guard
        logger.warning("research_library: DB import failed: %s", e)
        return []

    db = SessionLocal()
    try:
        q = (
            db.query(Document)
            .filter(Document.is_active == True)  # noqa: E712
            .filter(or_(Document.archived == False, Document.archived.is_(None)))  # noqa: E712
            .filter(Document.owner == owner)
            .order_by(Document.updated_at.desc())
        )
        total = q.count()
        rows = q.limit(_MAX_CANDIDATE_DOCS).all()
        if total > _MAX_CANDIDATE_DOCS:
            logger.info(
                "research_library: %d docs in library, considering the %d most recent",
                total, _MAX_CANDIDATE_DOCS,
            )
        out = []
        for d in rows:
            content = (d.current_content or "").strip()
            if not content and not (d.title or "").strip():
                continue
            out.append({"id": d.id, "title": d.title or "Untitled", "content": content})
        return out
    except Exception as e:
        logger.warning("research_library: failed to load documents: %s", e)
        return []
    finally:
        db.close()


def _keyword_score(query: str, text: str) -> float:
    """Fallback relevance: fraction of distinct query terms present in text."""
    terms = {t for t in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(t) > 2}
    if not terms:
        return 0.0
    low = (text or "").lower()
    hits = sum(1 for t in terms if t in low)
    return hits / len(terms)


def _semantic_rank(
    question: str, docs: List[Dict], max_docs: int, min_score: float
) -> Optional[List[Dict]]:
    """Rank docs by best-chunk cosine similarity to the question.

    Returns None if the embedding backend is unavailable (caller falls back to
    keyword scoring).
    """
    try:
        from src.embeddings import get_embedding_client
    except Exception:
        return None
    client = get_embedding_client()
    if client is None:
        return None

    # Build the chunk list, remembering which doc each chunk belongs to.
    chunk_texts: List[str] = []
    chunk_owner: List[int] = []
    for di, d in enumerate(docs):
        body = d["title"] + "\n" + d["content"]
        for ch in _chunk_text(body):
            chunk_texts.append(ch)
            chunk_owner.append(di)
            if len(chunk_texts) >= _MAX_TOTAL_CHUNKS:
                break
        if len(chunk_texts) >= _MAX_TOTAL_CHUNKS:
            logger.info(
                "research_library: hit global chunk cap (%d); some documents only partially scored",
                _MAX_TOTAL_CHUNKS,
            )
            break
    if not chunk_texts:
        return []

    try:
        import numpy as np
        vecs = client.encode([question] + chunk_texts, normalize_embeddings=True)
    except TypeError:
        # Some clients don't accept the kwarg.
        import numpy as np
        vecs = client.encode([question] + chunk_texts)
    except Exception as e:
        logger.warning("research_library: embedding failed (%s); using keyword fallback", e)
        return None

    if vecs is None or len(vecs) < 2:
        return None
    qv = vecs[0]
    # Best chunk score + index per document.
    best_score: Dict[int, float] = {}
    best_chunk: Dict[int, int] = {}
    for ci, cv in enumerate(vecs[1:]):
        di = chunk_owner[ci]
        score = float(np.dot(qv, cv))
        if score > best_score.get(di, -1.0):
            best_score[di] = score
            best_chunk[di] = ci

    ranked = sorted(best_score.items(), key=lambda kv: kv[1], reverse=True)
    results = []
    for di, score in ranked:
        if score < min_score:
            continue
        d = docs[di]
        excerpt = chunk_texts[best_chunk[di]]
        results.append({"doc": d, "score": round(score, 4), "excerpt": excerpt})
        if len(results) >= max_docs:
            break
    return results


def retrieve_library_findings(
    question: str,
    owner: str,
    max_docs: int = 5,
    min_score: float = _DEFAULT_MIN_SCORE,
) -> List[Dict]:
    """Retrieve the most relevant Library documents as research findings.

    Returns a list of finding dicts compatible with DeepResearcher's findings:
        {url, title, summary, evidence, og_image, source_type, score}
    The `url` uses an `odysseus://library/<id>` pseudo-scheme so the document
    shows up in the report's Sources list labelled as a Library source.

    Never raises — returns [] on any problem so the research loop is unaffected.
    """
    try:
        max_docs = max(1, min(int(max_docs or 5), 25))
    except Exception:
        max_docs = 5

    if not question or not owner:
        return []

    docs = _load_candidate_docs(owner)
    if not docs:
        return []

    ranked = _semantic_rank(question, docs, max_docs, min_score)
    method = "semantic"
    if ranked is None:
        # Embedding backend unavailable — degrade to keyword overlap.
        method = "keyword"
        scored = []
        for d in docs:
            s = _keyword_score(question, d["title"] + "\n" + d["content"])
            if s > 0:
                scored.append((d, s))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        ranked = [
            {"doc": d, "score": round(s, 4), "excerpt": (d["content"] or d["title"])[:_CHUNK_CHARS]}
            for d, s in scored[:max_docs]
        ]

    findings: List[Dict] = []
    for r in ranked:
        d = r["doc"]
        excerpt = (r.get("excerpt") or "").strip()
        full = (d["content"] or "").strip()
        findings.append({
            "url": f"odysseus://library/{d['id']}",
            "title": d["title"],
            "summary": excerpt[:_SUMMARY_CHARS] or (d["title"]),
            "evidence": (excerpt if len(excerpt) >= len(full) else full)[:_EVIDENCE_CHARS],
            "og_image": "",
            "source_type": "library",
            "rational": "Retrieved from your Library by semantic relevance",
            "score": r.get("score", 0.0),
        })

    logger.info(
        "research_library: %s retrieval selected %d/%d docs for owner=%s",
        method, len(findings), len(docs), owner,
    )
    return findings
