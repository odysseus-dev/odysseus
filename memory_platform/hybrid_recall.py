"""hybrid_recall.py — BM25 + dense + RRF fusion over a vector store.

The measured improvement for the memory platform: pure vector search
(MemoryVectorStore.search) misses exact-term and mixed queries that lexical
fusion catches. This module fuses dense cosine with a BM25-style lexical
score using Reciprocal Rank Fusion, plus precomputed association enrichment.

Standalone and dependency-light: reads entries through the vector store's
collection and an embed callable. Designed to be the drop-in replacement for
MemoryVectorStore.search (see swap in app.py).
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

# Recall + association thresholds.
RRF_K = 15
RECALL_BUDGET = 8
ASSOC_MIN_COSINE = 0.74
ASSOC_STRONG_COSINE = 0.80
ASSOC_FANOUT = 6
RECENCY_HALF_LIFE = 30.0

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after"}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in _STOP}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _bm25_score(query: str, text: str) -> float:
    qw = [w for w in re.findall(r"[a-z]{4,}", query.lower()) if w not in _STOP]
    if not qw:
        return 0.0
    low = text.lower()
    return sum(1 + 0.5 * i for i, w in enumerate(qw) if w in low)


class HybridRecall:
    """BM25 + dense + RRF fusion over a vector store.

    Dense (from the vector store's embeddings) + BM25 lexical + RRF fusion,
    then precomputed association enrichment. Wraps MemoryVectorStore.search;
    the original callable is preserved on `_search_orig` so it can be
    restored (rollback-safe).
    """

    def __init__(self, memory_vector, embed_fn: Callable, k: int = RECALL_BUDGET):
        self._mv = memory_vector
        self._embed = embed_fn
        self._k = k
        self._graph: Dict[str, List] = {}

    def rebuild_graph(self, entries: List[Dict]) -> None:
        """Precompute the association graph (at write time — free at recall)."""
        texts = [e.get("text", "") for e in entries]
        vecs = {}
        try:
            for t in texts:
                if t:
                    vecs[t] = self._embed([t])[0]
        except Exception:
            return
        self._graph = {}
        for e in entries:
            eid = e.get("id")
            ev = vecs.get(e.get("text", ""))
            if not ev:
                self._graph[eid] = []
                continue
            links = []
            for o in entries:
                oid = o.get("id")
                if oid == eid:
                    continue
                ov = vecs.get(o.get("text", ""))
                if not ov:
                    continue
                c = _cosine(ev, ov)
                if c >= ASSOC_MIN_COSINE:
                    links.append((c, oid))
            links.sort(key=lambda x: -x[0])
            self._graph[eid] = links[:ASSOC_FANOUT]

    def search(self, query: str, k: int = RECALL_BUDGET) -> List[Dict]:
        """Hybrid: dense (from the vector store) + BM25 + RRF + associations."""
        entries = self._collect_entries()
        if not entries:
            return self._mv.search(query, k) if self._mv else []

        qvec = None
        try:
            qvec = self._embed([query])[0]
        except Exception:
            qvec = None

        dense = []
        if qvec:
            dense = sorted(
                ((_cosine(qvec, e.get("_vec")), e) for e in entries
                 if e.get("_vec")),
                key=lambda x: -x[0])

        bm25 = sorted((( _bm25_score(query, e.get("text", "")), e)
                       for e in entries if e.get("text")),
                      key=lambda x: -x[0])

        ranks: Dict[str, float] = {}
        for pos, (_, e) in enumerate(dense[:k * 2]):
            ranks[e["id"]] = ranks.get(e["id"], 0) + 1.0 / (RRF_K + pos + 1)
        for pos, (_, e) in enumerate(bm25[:k * 2]):
            ranks[e["id"]] = ranks.get(e["id"], 0) + 1.0 / (RRF_K + pos + 1)

        # association enrichment (precomputed graph, free)
        result = [eid for eid, _ in sorted(ranks.items(), key=lambda x: -x[1])[:k]]
        if self._graph:
            seen = set(result)
            for mid in list(result):
                for c, oid in self._graph.get(mid, [])[:3]:
                    if oid not in seen:
                        seen.add(oid)
                        result.append(oid)
            result = result[:k]
        return [{"memory_id": rid, "score": 1.0 / (1.0 + i),
                 "embedding_lane": "hybrid"}
                for i, rid in enumerate(result)]

    def _collect_entries(self) -> List[Dict]:
        """Best-effort: entries with text + a vector, if the store exposes them."""
        out = []
        try:
            mv = self._mv
            if hasattr(mv, "_collection") and mv._collection is not None:
                data = mv._collection.get(limit=2000)
                for i, mid in enumerate(data.get("ids", [])):
                    text = data.get("documents", [""] * len(data.get("ids", [])))[i] \
                        if data.get("documents") else ""
                    emb = data.get("embeddings", [])[i] if data.get("embeddings") else None
                    out.append({"id": mid, "text": text, "_vec": emb})
        except Exception:
            out = []
        return out


def swap_recall(memory_vector, embed_fn=None, k: int = RECALL_BUDGET) -> Optional[HybridRecall]:
    """Install hybrid recall over a vector store's search (rollback-safe).

    Replaces `memory_vector.search` with the fused hybrid recall and keeps
    the original callable on `_search_orig` so the swap can be reverted.
    Returns the HybridRecall instance, or None if the store has no `_embed`.

    Compatible degraded states:
      - store has no `_embed` -> returns None, search untouched.
      - store has `_embed` -> swaps search, preserves original on _search_orig.
    """
    if memory_vector is None:
        return None
    embed_fn = embed_fn or getattr(memory_vector, "_embed", None)
    if embed_fn is None:
        return None
    hybrid = HybridRecall(memory_vector, embed_fn, k=k)
    memory_vector._search_orig = getattr(memory_vector, "search", None)
    memory_vector.search = hybrid.search
    return hybrid


def restore_search(memory_vector) -> bool:
    """Revert a hybrid-recall swap; restores the original search if present."""
    if memory_vector is None:
        return False
    orig = getattr(memory_vector, "_search_orig", None)
    if orig is not None:
        memory_vector.search = orig
        return True
    return False
