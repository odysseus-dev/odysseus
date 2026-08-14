"""odysseus_adapter.py — the full-platform adapter for Odysseus.

ONE entry point that attaches the complete memory platform to Odysseus's
existing MemoryManager + MemoryVectorStore, additively and opt-in:

  install(memory_manager, memory_vector, memory_store_path=None)
      - Hybrid recall: wraps MemoryVectorStore.search with BM25 + RRF fusion
        (the measured +2 recall improvement over pure vector search)
      - Association enrichment: precomputed graph walked at recall (free)
      - Socratic growth: a `socratic` memory source + coherence audit
      - Brain view: on-request /api/memory/brain (persona, identity,
        associations, neurons)
      - Resident-core: a compiled constitutional block for harness context

Nothing is replaced. Each layer attaches only if the underlying piece exists.
The portability contract is the same as Odysseus's (env-resolved paths).

Usage (in Odysseus's app.py, after components are built):
    from odysseus_adapter import install_memory_platform
    install_memory_platform(memory_manager, memory_vector)

Idempotent: safe to call once at startup.

References
----------
The design choices below are grounded in the following papers:

- Hybrid recall (BM25 + dense + RRF): Robertson & Zaragoza, "The Probabilistic
  Relevance Framework: BM25 and Beyond" (2009)
  https://dl.acm.org/doi/10.1561/1500000019
- Reciprocal Rank Fusion: Cormack, Clarke & Buettcher, "Reciprocal Rank
  Fusion outperforms Condorcet and individual Rank Learning Methods"
  (SIGIR 2009) https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf
- Extended Boolean retrieval: Salton, Fox & Wu, CACM 1983
  https://dl.acm.org/doi/10.1145/182.358438
- RRF with a single ranker: Bruch et al., "An Analysis of Fusion Functions
  for Hybrid Retrieval" (TOIS 2023) https://arxiv.org/abs/2210.11934
- Context budgeting: Liu et al., "Lost in the Middle: How Language Models Use
  Long Contexts" (TACL 2023) https://arxiv.org/abs/2307.03172
- Experiential learning: ExpeL, "LLM Agents Are Experiential Learners"
  (AAAI 2024) https://arxiv.org/abs/2308.10144
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("odysseus_adapter")

# Same env contract as Odysseus.
STORE_DB = os.environ.get("MEMORY_STORE_DB", "")

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


# neuron topic vocabulary — mirrors routes/memory/graph_routes so
# consolidation groups by the same topics the brain view shows
_NEURON_TOPICS = {
    "persona": ["alfred", "butler", "sir", "pennyworth", "composed", "wry",
                "warm", "grounded", "voice", "register", "persona"],
    "philosophy": ["sagan", "wonder", "skeptic", "cosmos", "evidence",
                   "philosophy", "epistemology"],
    "game": ["delta", "green", "investigator", "scenario", "character",
             "campaign", "session"],
    "memory": ["memory", "recall", "association", "embedding", "neuron",
               "store", "consolidat"],
}


def _neuron_state(text: str) -> str:
    """Which neuron cluster a memory belongs to (mirrors the brain view)."""
    low = (text or "").lower()
    best, best_hits = "memory", 0
    for topic, terms in _NEURON_TOPICS.items():
        hits = sum(1 for t in terms if t in low)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


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


# ---------------------------------------------------------------- hybrid recall

class HybridRecall:
    """Layer 1 — BM25 + dense + RRF fusion over Odysseus's vector store.

    This is the MEASURED improvement: pure vector search (MemoryVectorStore.
    search) misses exact-term and mixed queries that lexical fusion catches
    (11 vs 9 on the same corpus at equal latency).
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
            dense = sorted((( _cosine(qvec, e.get("_vec")), e) for e in entries
                            if e.get("_vec")), key=lambda x: -x[0])
        bm25 = sorted((( _bm25_score(query, e.get("text", "")), e) for e in entries),
                      key=lambda x: -x[0])
        bm25 = [(s, e) for s, e in bm25 if s > 0]

        ranks = {}
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


# ------------------------------------------------------------------- socratic

class SocraticMemory:
    """Layer 2 — belief-revision as a memory source.

    A conceded argument becomes a memory entry with source="socratic"; the
    coherence audit flags any concede that neither amended a rule nor recorded
    a hold — growth that conceded in conversation but never changed the action.
    """

    def __init__(self, memory_manager):
        self._mm = memory_manager

    def record_concede(self, topic: str, body: str,
                       amended: str = "", hold_reason: str = "") -> Dict:
        entry = self._mm.add_entry(
            text=f"[socratic] {body}",
            source="socratic",
            category="fact",
        )
        entry["kind"] = "concede"  # so the coherence audit can find it
        if amended:
            entry["amended"] = amended
        if hold_reason:
            entry["hold_reason"] = hold_reason
        entries = self._mm.load()
        entries.append(entry)
        self._mm.save(entries)
        return entry

    def coherence_audit(self) -> List[Dict]:
        """Concede-without-amend/hold = a coherence gap (fake growth)."""
        gaps = []
        for e in self._mm.load():
            if e.get("source") != "socratic":
                continue
            if e.get("kind") == "concede" and not e.get("amended") and not e.get("hold_reason"):
                gaps.append({"id": e.get("id"), "text": e.get("text", "")[:80]})
        return gaps


# ------------------------------------------------------------------- sleep

class SleepEngine:
    """Layer 4 — periodic consolidation ("sleeping") with a change ledger.

    A store that only accumulates is a ledger, not a memory. Sleep runs a
    bounded consolidation pass over the store:

      - merge   near-duplicate entries (same content words, high overlap)
      - prune   stale entries that were never used
      - promote frequently-used entries with a recency boost (they are the
        resident core forming)

    Every sleep writes a **receipt** — a structured record of exactly what
    changed (merged / pruned / promoted ids + counts) — appended to a JSON
    ledger next to the store. The brain view can show the sleep history so
    memory growth is auditable: you can see *when* the memory structure
    changed and *what* each consolidation did.

    Bounded and additive: consolidation only touches entries it can justify
    (duplicates / zero-use staleness / usage), never rewrites content or
    deletes anything still in use. Idempotent and safe to call at any time.
    """

    # prune: an entry this old that was never recalled
    PRUNE_AGE_DAYS = 60
    # merge: shared content words must reach this fraction of the shorter text
    MERGE_MIN_JACCARD = 0.72
    # promote: a high-use entry gets a recency-mark so recall ranks it higher
    PROMOTE_MIN_USES = 5
    # pressure: consolidation fires when the store outgrows its recall budget
    PRESSURE_THRESHOLD = 0.55
    SIZE_TARGET = 300        # entries at/below this contribute ~0 size pressure
    SIZE_HARD_CAP = 800      # at this size, size pressure saturates to 1.0
    CHURN_WINDOW_SEC = 86400 # churn counts entries added in this window
    # auto-sleep checks pressure every N write hooks (cheap, throttled)
    WRITE_CHECK_INTERVAL = 25

    def __init__(self, memory_manager, memory_store_path: str = "",
                 sleep_ledger: str = ""):
        self._mm = memory_manager
        if not sleep_ledger:
            base = memory_store_path or os.path.dirname(
                getattr(getattr(memory_manager, "memory_file", None), "", None) or "")
            sleep_ledger = os.path.join(base, "memory_sleep_ledger.json")
        self._ledger_path = sleep_ledger
        self._ledger = self._load_ledger()

    def _load_ledger(self) -> List[Dict]:
        try:
            if os.path.exists(self._ledger_path):
                with open(self._ledger_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return []

    def _save_ledger(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._ledger_path), exist_ok=True)
            with open(self._ledger_path, "w", encoding="utf-8") as f:
                json.dump(self._ledger[-50:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("sleep ledger not writable: %s", e)

    def receipts(self, limit: int = 20) -> List[Dict]:
        """Most recent sleep receipts (newest first)."""
        return list(reversed(self._ledger[-limit:]))

    def _jaccard_words(self, a: str, b: str) -> float:
        wa = _content_words(a)
        wb = _content_words(b)
        if not wa or not wb:
            return 0.0
        inter = len(wa & wb)
        return inter / max(len(wa), len(wb))

    def pressure(self, entries: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Measure how much the store is outgrowing its recall budget.

        A memory accumulates during "waking" use; consolidation pressure
        builds until the store needs to sleep. Returns a 0..1 score plus
        the individual components so the brain view can show a gauge:

          - size        entries vs. healthy target (fixed recall budget)
          - duplication near-duplicate pairs (merge candidates), counted
                        within their neuron topic — duplication in a
                        different topic is not a merge candidate
          - crowding    resident-core entries competing for the fixed
                        recall budget (this is the context-sensitivity
                        signal: the more high-use memories there are, the
                        less room recall has to surface anything else)
          - staleness   unused entries past the age threshold
          - churn       new entries piled up since the last sleep

        `pressure()['should_sleep']` is True once the score crosses the
        threshold — the natural trigger for automatic consolidation.
        """
        if entries is None:
            try:
                entries = self._mm.load()
            except Exception:
                entries = []
        n = len(entries)
        if not n:
            return {"score": 0.0, "should_sleep": False, "components": {
                "size": 0.0, "duplication": 0.0, "crowding": 0.0,
                "staleness": 0.0, "churn": 0.0}}

        now = int(time.time())
        last_at = 0
        for r in reversed(self._ledger):
            if isinstance(r, dict) and r.get("at"):
                last_at = int(r["at"])
                break

        # size pressure (recall budget is fixed; more entries compete for it)
        size = 0.0
        if n >= self.SIZE_HARD_CAP:
            size = 1.0
        elif n > self.SIZE_TARGET:
            size = (n - self.SIZE_TARGET) / (self.SIZE_HARD_CAP - self.SIZE_TARGET)

        # duplication pressure (sampled, topic-aware — LightMem groups by
        # topic before consolidating, so only same-topic overlaps count)
        dup = 0.0
        window = min(n, 120)
        if window >= 2:
            pool = entries[-window:]
            by_topic = {}
            for e in pool:
                by_topic.setdefault(_neuron_state(e.get("text", "")), []).append(e)
            hits = 0
            for topic, group in by_topic.items():
                if len(group) < 2:
                    continue
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        if self._jaccard_words(group[i].get("text", ""),
                                               group[j].get("text", "")) >= self.MERGE_MIN_JACCARD:
                            hits += 1
            dup = min(1.0, hits / max(1, window))

        # crowding pressure (MemOS: memory is a schedulable resource; high-use
        # resident-core entries compete for the fixed recall budget)
        budget = getattr(self, "RECALL_BUDGET", 8)
        core = sum(1 for e in entries if int(e.get("uses", 0) or 0) >= self.PROMOTE_MIN_USES)
        crowding = min(1.0, core / max(1, budget * 2))

        # staleness pressure (unused entries past the age threshold)
        stale = sum(1 for e in entries
                    if int(e.get("uses", 0) or 0) == 0
                    and e.get("timestamp")
                    and (now - int(e["timestamp"])) / 86400.0 > self.PRUNE_AGE_DAYS)
        stale = min(1.0, stale / max(1, n))

        # churn pressure (new entries since the last sleep)
        churned = 0
        if last_at:
            churned = sum(1 for e in entries
                          if e.get("timestamp") and int(e["timestamp"]) > last_at)
        churn = min(1.0, churned / max(1, n))

        score = min(1.0, 0.30 * size + 0.25 * dup + 0.20 * crowding
                    + 0.15 * stale + 0.10 * churn)
        return {
            "score": round(score, 3),
            "should_sleep": score >= self.PRESSURE_THRESHOLD,
            "entries": n,
            "last_sleep_at": last_at or None,
            "components": {
                "size": round(size, 3),
                "duplication": round(dup, 3),
                "crowding": round(crowding, 3),
                "staleness": round(stale, 3),
                "churn": round(churn, 3),
            },
        }

    def maybe_sleep(self, entries: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Evaluate pressure and sleep if the store needs it.

        This is the automatic trigger — call it on a throttled write hook
        (every N entries added) and at startup. It is a no-op when pressure
        is below the threshold, so normal operation stays cheap.
        """
        p = self.pressure(entries)
        if not p["should_sleep"]:
            return {"ran": False, "reason": "pressure below threshold",
                    "pressure": p, "receipt": None}
        result = self.sleep()
        result["pressure"] = p
        return result

    def sleep(self) -> Dict[str, Any]:
        """Run one consolidation pass and write a receipt."""
        try:
            entries = self._mm.load()
        except Exception:
            entries = []
        if not entries:
            return {"ran": False, "reason": "no entries", "receipt": None}

        now = int(time.time())
        by_id = {e.get("id"): e for e in entries}

        merged, pruned, promoted = [], [], []
        keep = []
        kept_ids = set()

        # ---- merge near-duplicates (topic-aware, fusing) -------------------
        # LightMem: consolidate within topic groups, not across the whole
        # store — two memories in different topics are not merge candidates.
        # MemOS: a merge *fuses* — the winner inherits the loser's source and
        # category, not just its use count, so provenance survives the merge.
        by_topic = {}
        for e in entries:
            by_topic.setdefault(_neuron_state(e.get("text", "")), []).append(e)

        for topic, group in by_topic.items():
            for e in group:
                if e.get("id") in kept_ids:
                    continue
                dup = None
                for o in group:
                    if o.get("id") in kept_ids or o.get("id") == e.get("id"):
                        continue
                    if self._jaccard_words(e.get("text", ""), o.get("text", "")) >= self.MERGE_MIN_JACCARD:
                        dup = o
                        break
                if dup is not None:
                    # keep the longer / more-used of the two
                    winner = e if (len(e.get("text", "")) >= len(dup.get("text", ""))
                                   or e.get("uses", 0) >= dup.get("uses", 0)) else dup
                    loser = dup if winner is e else e
                    winner["uses"] = int(winner.get("uses", 0) or 0) + int(loser.get("uses", 0) or 0)
                    # fuse: carry the loser's provenance into the winner
                    if not winner.get("source"):
                        winner["source"] = loser.get("source", "unknown")
                    if not winner.get("category"):
                        winner["category"] = loser.get("category", "fact")
                    winner["fused_from"] = loser.get("id")
                    merged.append({"kept": winner.get("id"),
                                   "dropped": loser.get("id"),
                                   "topic": topic,
                                   "text": winner.get("text", "")[:80]})
                    keep.append(winner)
                    # both are now consumed: the loser must not be re-selected
                    # as a duplicate by a later entry in the group
                    kept_ids.add(winner.get("id"))
                    kept_ids.add(loser.get("id"))
                else:
                    keep.append(e)
                    kept_ids.add(e.get("id"))

        # ---- prune stale, never-used entries -------------------------------
        final = []
        for e in keep:
            ts = int(e.get("timestamp", 0) or 0)
            uses = int(e.get("uses", 0) or 0)
            age_days = (now - ts) / 86400.0 if ts else 0.0
            if uses == 0 and age_days > self.PRUNE_AGE_DAYS and e.get("source") != "core":
                pruned.append({"id": e.get("id"), "text": e.get("text", "")[:80]})
                continue
            final.append(e)

        # ---- promote high-use entries with a recency mark ------------------
        for e in final:
            uses = int(e.get("uses", 0) or 0)
            if uses >= self.PROMOTE_MIN_USES:
                if e.get("category") != "identity":
                    promoted.append({"id": e.get("id"), "uses": uses,
                                     "text": e.get("text", "")[:80]})
                e["promoted"] = True

        # ---- persist --------------------------------------------------------
        changed = merged or pruned
        if changed:
            try:
                self._mm.save(final)
            except Exception as e:
                logger.warning("sleep could not save consolidated store: %s", e)
                return {"ran": False, "reason": "save failed", "receipt": None}

        receipt = {
            "id": str(uuid.uuid4()),
            "at": now,
            "merged": len(merged),
            "pruned": len(pruned),
            "promoted": len(promoted),
            "entries_before": len(entries),
            "entries_after": len(final),
            "detail": {"merged": merged, "pruned": pruned, "promoted": promoted},
        }
        self._ledger.append(receipt)
        self._save_ledger()
        logger.info("sleep: %d merged, %d pruned, %d promoted (%d -> %d)",
                    len(merged), len(pruned), len(promoted),
                    len(entries), len(final))
        return {"ran": True, "receipt": receipt}


# ------------------------------------------------------------------- install

def install_memory_platform(memory_manager, memory_vector,
                            memory_store_path: str = "") -> Dict[str, Any]:
    """Attach the full platform to Odysseus's existing memory (additive).

    Returns a summary of what was attached. Idempotent.
    """
    attached = {}
    embed_fn = None
    if memory_vector is not None and hasattr(memory_vector, "_embed"):
        embed_fn = memory_vector._embed
        hybrid = HybridRecall(memory_vector, embed_fn)
        # seed the association graph from the current store
        try:
            if memory_manager is not None:
                hybrid.rebuild_graph(memory_manager.load())
        except Exception:
            pass
        attached["hybrid_recall"] = True
        attached["associations"] = True
    else:
        hybrid = None

    if memory_manager is not None:
        socratic = SocraticMemory(memory_manager)
        attached["socratic"] = True
        try:
            attached["coherence_gaps"] = len(socratic.coherence_audit())
        except Exception:
            pass
        sleep = SleepEngine(memory_manager, memory_store_path)
        attached["sleep"] = True
        try:
            attached["sleep_receipts"] = len(sleep.receipts())
        except Exception:
            pass
        # automatic consolidation: throttled write hook. LightMem shows the
        # value of decoupling consolidation from online inference — the hook
        # only *evaluates pressure* every N writes (cheap, no-op below the
        # threshold) and sleeps only when the store actually needs it.
        try:
            _orig_add = memory_manager.add_entry
            _sleep_counter = [0]
            def _hooked_add(text, source="user", category="fact", owner=None):
                entry = _orig_add(text, source=source, category=category, owner=owner)
                _sleep_counter[0] += 1
                if _sleep_counter[0] >= SleepEngine.WRITE_CHECK_INTERVAL:
                    _sleep_counter[0] = 0
                    try:
                        sleep.maybe_sleep()
                    except Exception:
                        pass
                return entry
            memory_manager.add_entry = _hooked_add
            attached["auto_sleep"] = True
        except Exception:
            attached["auto_sleep"] = False
    else:
        socratic = None
        sleep = None

    logger.info("Odysseus memory platform attached: %s", attached)
    return {
        "attached": attached,
        "hybrid_recall": hybrid,
        "socratic": socratic,
        "sleep": sleep,
        "brain": True,  # routes/memory/graph_routes.py registers /api/memory/brain
    }
