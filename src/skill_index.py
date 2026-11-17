"""Semantic ranking for the native Odysseus skill registry.

This deliberately reuses an already-ready ToolIndex embedding lane. Skill search
must never trigger a cold embedding-model load in the foreground request path;
the existing lexical matcher remains the fallback until startup prewarm finishes.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Sequence

from src.embedding_lanes import LANE_FASTEMBED

logger = logging.getLogger(__name__)

_CACHE_LIMIT = 16
_cache_lock = threading.Lock()
_corpus_cache: "OrderedDict[str, List[List[float]]]" = OrderedDict()


def _semantic_text(skill: Dict[str, Any]) -> str:
    parts = [
        f"Skill: {skill.get('name', '')}",
        str(skill.get("description") or skill.get("title") or ""),
        f"Use when: {skill.get('when_to_use') or skill.get('problem') or ''}",
    ]
    tags = skill.get("tags") or []
    if tags:
        parts.append("Tags: " + ", ".join(str(tag) for tag in tags[:24]))
    procedure = skill.get("procedure") or skill.get("steps") or []
    if procedure:
        parts.append("Procedure: " + " ".join(str(step) for step in procedure[:8]))
    # Imported skills can be very large. Ranking needs intent, not full reference
    # material or bundled templates.
    return "\n".join(parts)[:6000]


def _choose_lane(index: Any) -> Any:
    lanes = list(getattr(index, "embedding_lanes", ()) or ())
    return next((lane for lane in lanes if getattr(lane, "name", "") == LANE_FASTEMBED), None) or (
        lanes[0] if lanes else None
    )


def _cache_key(lane: Any, documents: Sequence[str]) -> str:
    lane_id = str(getattr(lane, "fingerprint", "") or getattr(lane, "name", "unknown"))
    digest = hashlib.sha256()
    digest.update(lane_id.encode("utf-8"))
    for document in documents:
        digest.update(b"\0")
        digest.update(document.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _document_vectors(lane: Any, documents: Sequence[str]) -> List[List[float]]:
    key = _cache_key(lane, documents)
    with _cache_lock:
        cached = _corpus_cache.get(key)
        if cached is not None:
            _corpus_cache.move_to_end(key)
            return cached

    vectors = [list(vector) for vector in lane.encode(documents)]
    with _cache_lock:
        _corpus_cache[key] = vectors
        _corpus_cache.move_to_end(key)
        while len(_corpus_cache) > _CACHE_LIMIT:
            _corpus_cache.popitem(last=False)
    return vectors


def semantic_skill_scores(query: str, skills: Sequence[Dict[str, Any]]) -> Dict[int, float]:
    """Return cosine-like scores keyed by input position, or ``{}`` on fallback."""
    if not query.strip() or not skills:
        return {}
    try:
        # This accessor has no initialization side effect. Startup owns model
        # loading; foreground skill matching remains fast and deterministic.
        from src.tool_index import get_ready_tool_index

        index = get_ready_tool_index()
        if index is None:
            return {}
        lane = _choose_lane(index)
        if lane is None:
            return {}

        documents = [_semantic_text(skill) for skill in skills]
        vectors = _document_vectors(lane, documents)
        query_vectors = lane.encode([query[:4000]])
        if not query_vectors:
            return {}
        query_vector = list(query_vectors[0])

        scores: Dict[int, float] = {}
        for index_position, vector in enumerate(vectors):
            if len(vector) != len(query_vector) or not vector:
                continue
            # EmbeddingLane.encode requests normalized vectors, so their dot
            # product is cosine similarity without another numerical dependency.
            score = sum(float(left) * float(right) for left, right in zip(query_vector, vector))
            scores[index_position] = max(-1.0, min(1.0, score))
        return scores
    except Exception as exc:
        logger.debug("Semantic skill ranking unavailable; using lexical fallback: %s", exc)
        return {}


def reset_skill_index_cache() -> None:
    """Clear process-local vectors after embedding configuration changes/tests."""
    with _cache_lock:
        _corpus_cache.clear()
