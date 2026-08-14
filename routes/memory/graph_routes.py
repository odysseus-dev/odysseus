"""routes/memory/graph_routes.py — the brain view (system-state overview).

DIAGNOSTIC OVERVIEW, NOT recall.

This endpoint renders a living picture of the memory system when the user
opens the Brain view: where the persona layer and identity are forming, the
association graph, and how neurons are firing. It reflects the current state
of the system — how things are linked through the network — on request.

  GET /api/memory/brain
    Returns a snapshot:
      persona      — the persona-layer entries (the "voice")
      identity     — the identity values (who it is becoming)
      associations — the association graph (nodes + edges, how memories link)
      neurons      — the warm neurons and their firing state
      links        — how data points connect through the neuron network

This is a pure view. It never mutates memory and never participates in recall.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Request

ASSOC_MIN_COSINE = 0.74
ASSOC_STRONG_COSINE = 0.80
ASSOC_FANOUT = 6

_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after"}

# neuron trigger vocabulary (mirrors warm_router) for firing-state display
_NEURON_TOPICS = {
    "persona": ["alfred", "butler", "sir", "pennyworth", "composed", "wry"],
    "philosophy": ["sagan", "wonder", "skeptic", "cosmos", "evidence"],
    "game": ["delta green", "ttrpg", "carcosa", "yellow king"],
    "memory": ["memory", "recall", "association", "embedding", "neuron"],
}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())
            if w not in _STOP}


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _distinctive(shared: set, corpus: List[str], df_floor: float = 0.05) -> set:
    total = max(len(corpus), 1)
    joined = " ".join(t.lower() for t in corpus)
    return {w for w in shared if joined.count(f" {w} ") / total < df_floor}


def _neuron_state(text: str) -> str:
    """Which neuron cluster a memory belongs to (for the brain view)."""
    low = (text or "").lower()
    best, best_hits = "memory", 0
    for topic, terms in _NEURON_TOPICS.items():
        hits = sum(1 for t in terms if t in low)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


def _node_type(category: str) -> str:
    """Classify a memory entry into the graph's node tiers.

    - core    — persona / identity / delivery: what the agent is becoming.
    - skill   — reusable knowledge / capability modules.
    - external — store facts, projects, preferences: recallable content.
    """
    cat = (category or "").lower()
    if cat in ("persona", "identity", "delivery", "agent", "self"):
        return "core"
    if cat in ("skill", "skill_knowledge", "module", "tool", "capability", "knowledge"):
        return "skill"
    return "external"


def build_brain_snapshot(
    entries: List[Dict],
    embeddings=None,
    persona_categories=("persona", "identity", "delivery"),
    warm_neurons=None,
) -> Dict[str, Any]:
    """Assemble the system-state overview for the Brain view.

    `entries`: current memory entries [{id, text, category}].
    `embeddings`: callable text->vector for association computation.
    `warm_neurons`: optional list of {slug, body} for firing state.
    """
    # vectors (only needed for the association edges)
    vecs = {}
    if embeddings is not None:
        try:
            texts = [e.get("text", "") for e in entries if e.get("text")]
            for t in texts:
                vecs[t] = embeddings([t])[0]
        except Exception:
            pass

    # persona + identity layers (what's forming)
    persona = [{"id": e.get("id"), "text": e.get("text", "")[:80]}
               for e in entries if e.get("category") in persona_categories]
    identity = [{"id": e.get("id"), "text": e.get("text", "")[:80]}
                for e in entries if e.get("category") == "identity"]

    # association graph (bounded)
    nodes = [{
        "id": e.get("id"),
        "label": e.get("text", "")[:28],
        "category": e.get("category", "fact"),
        "neuron": _neuron_state(e.get("text", "")),
        "type": _node_type(e.get("category", "fact")),
        "length": len(e.get("text", "")),
    } for e in entries]
    by_id = {e.get("id"): e for e in entries}
    links = {e.get("id"): [] for e in entries}
    for e in entries:
        eid = e.get("id")
        ev = vecs.get(e.get("text", ""))
        if not ev:
            continue
        cands = []
        for o in entries:
            oid = o.get("id")
            if oid == eid:
                continue
            ov = vecs.get(o.get("text", ""))
            if not ov:
                continue
            cos = _cosine(ev, ov)
            if cos >= ASSOC_STRONG_COSINE:
                cands.append((cos, oid, 1))
                continue
            if cos < ASSOC_MIN_COSINE:
                continue
            shared = _content_words(e.get("text", "")) & _content_words(o.get("text", ""))
            if _distinctive(shared, [x.get("text", "") for x in entries]):
                cands.append((cos, oid, len(shared)))
        cands.sort(key=lambda x: -x[0])
        links[eid] = cands[:ASSOC_FANOUT]
    edges = []
    seen = set()
    for eid, cands in links.items():
        for cos, oid, _ in cands:
            key = tuple(sorted((eid, oid)))
            if key in seen:
                continue
            seen.add(key)
            edges.append({"a": eid, "b": oid, "s": round(cos, 3)})

    # neurons + firing state (curated character neurons are marked "active"
    # — the brain view shows which persona clusters are engaged)
    neurons = []
    if warm_neurons:
        for n in warm_neurons:
            slug = n.get("slug", "neuron")
            neurons.append({"slug": slug, "body": n.get("body", "")[:80],
                            "firing": slug in ("persona-alfred",
                                               "philosophy-sagan-core",
                                               "research-warm-memory")})
    else:
        neurons = [{"slug": t, "body": "", "firing": False}
                   for t in _NEURON_TOPICS]

    return {
        "persona": persona,
        "identity": identity,
        "associations": {"nodes": nodes, "edges": edges},
        "neurons": neurons,
        "links_count": len(edges),
        "node_count": len(nodes),
    }


def setup_graph_routes(memory_manager, memory_vector, warm_router=None,
                       sleep_engine=None) -> APIRouter:
    # Distinct prefix to avoid route-registration conflicts with the existing
    # /api/memory router (which also registers @router.get("") at the base).
    router = APIRouter(prefix="/api/memory-brain", tags=["memory-brain"])

    @router.get("/overview")
    def get_brain(request: Request) -> Dict[str, Any]:
        """On-request system overview: persona, identity, associations, neurons."""
        try:
            entries = memory_manager.load() if memory_manager else []
        except Exception:
            entries = []
        embed = None
        if memory_vector is not None and hasattr(memory_vector, "_embed"):
            embed = memory_vector._embed
        warm = None
        if warm_router is not None and hasattr(warm_router, "list_neurons"):
            try:
                warm = warm_router.list_neurons()
            except Exception:
                warm = None
        snapshot = {}
        try:
            snapshot = build_brain_snapshot(entries, embed, warm_neurons=warm)
        except Exception as e:
            snapshot = {"persona": [], "identity": [],
                        "associations": {"nodes": [], "edges": []},
                        "neurons": [], "links_count": 0, "node_count": 0,
                        "note": str(e)}
        # sleep history — when the memory structure changed and what each
        # consolidation did (diagnostic, read-only)
        if sleep_engine is not None:
            try:
                snapshot["sleep"] = {
                    "receipts": sleep_engine.receipts(limit=15),
                    "pressure": sleep_engine.pressure(),
                    "available": True,
                }
            except Exception:
                snapshot["sleep"] = {"receipts": [], "pressure": None,
                                     "available": False}
        return snapshot

    @router.get("/pressure")
    def get_pressure() -> Dict[str, Any]:
        """The consolidation-pressure gauge (read-only).

        A 0..1 score of how much the store is outgrowing its recall budget,
        with the components (size / duplication / crowding / staleness /
        churn) and whether the store should sleep. The Brain tab renders
        this as a pressure gauge.
        """
        if sleep_engine is None:
            return {"available": False, "score": None}
        try:
            return {"available": True, **sleep_engine.pressure()}
        except Exception as e:
            return {"available": False, "score": None, "note": str(e)}

    @router.post("/sleep")
    def run_sleep() -> Dict[str, Any]:
        """Run one consolidation pass; returns the receipt of what changed.

        Bounded and auditable: merges near-duplicates, prunes stale unused
        entries, and promotes high-use entries — then appends a receipt to
        the sleep ledger so memory growth is traceable.
        """
        if sleep_engine is None:
            return {"ran": False, "reason": "sleep engine not attached"}
        try:
            return sleep_engine.sleep()
        except Exception as e:
            return {"ran": False, "reason": str(e), "receipt": None}

    return router
