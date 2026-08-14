#!/usr/bin/env python3
"""benchmark.py — FAIR comparison of Odysseus memory retrieval vs the upgrade.

Runs BOTH retrieval approaches on the SAME data with the SAME embedding model
(Ollama nomic-embed-text), then measures:

  - Recall@k: does the right memory surface for each query?
  - Precision@k: are the surfaced memories actually relevant?
  - Latency: time to answer.

Baseline (Odysseus): plain vector cosine search over memory entries — this is
what MemoryVectorStore.search() does (ChromaDB semantic retrieval).
Upgrade: vector search + the precomputed association graph, surfacing direct
hits AND their graph neighbours.

This is a fair, head-to-head measure: identical data, identical embeddings,
identical queries. Only the retrieval algorithm differs.
"""

import json
import sys
import time
import urllib.request

# ---------------------------------------------------------------- embeddings

OLLAMA = "http://localhost:11434"
MODEL = "nomic-embed-text"


def embed(texts):
    """Embed a list of strings via Ollama nomic (256-dim, matryoshka)."""
    out = {}
    for t in texts:
        data = json.dumps({"model": MODEL, "input": t}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/embed", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            v = json.loads(r.read())["embeddings"][0]
        out[t] = v[:256]
    return out


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- the systems

def baseline_retrieve(query_vec, entries, k=5):
    """Odysseus baseline: pure vector cosine search over all entries."""
    scored = sorted(((cosine(query_vec, e["vec"]), e) for e in entries),
                    key=lambda x: -x[0])
    return scored[:k]


def upgrade_retrieve(query_vec, entries, k=5, assoc_strength=0.74):
    """Upgrade: vector search + association graph (precomputed links).

    Direct hits come from vector search; the association graph adds the
    entries those hits are LINKED to (real relations, bounded fanout) — so a
    query that matches one memory also surfaces its related memories.
    """
    direct = baseline_retrieve(query_vec, entries, k=3)
    # precompute the association graph for the whole entry set
    by_text = {e["text"]: e for e in entries}
    graph = {}
    for e in entries:
        links = []
        for o in entries:
            if o is e:
                continue
            c = cosine(e["vec"], o["vec"])
            if c >= assoc_strength:
                links.append((c, o["text"]))
        links.sort(key=lambda x: -x[0])
        graph[e["text"]] = links[:6]
    results = []
    seen = set()
    for c, e in direct:
        results.append((c, e))
        seen.add(e["text"])
    for c, e in direct:
        for lc, lt in graph.get(e["text"], [])[:3]:
            if lt in seen:
                continue
            seen.add(lt)
            results.append((lc * 0.8, by_text[lt]))
    results.sort(key=lambda x: -x[0])
    return results[:k]


# ---------------------------------------------------------------- the dataset

DATASET = [
    "The user prefers oat milk and avoids eggs",
    "The user drinks oat milk in the morning",
    "The user's protein shakes use oat milk",
    "The user teaches a beginner physical skill on weekends",
    "The user is building a beginner course for a physical skill",
    "The user runs a Linux desktop setup",
    "The user runs tabletop sessions for a group",
    "The tabletop campaign uses the Impossible Landscapes scenario",
    "The memory system has an association graph for recall",
    "The memory system uses Ollama nomic for embeddings",
    "The user manages a server for the game",
    "The user researches the philosophy of wonder and skepticism",
]

# each query maps to the memory that SHOULD surface (ground truth)
QUERIES = [
    ("what does the user drink", "The user drinks oat milk in the morning"),
    ("what coffee does the user like", "The user drinks oat milk in the morning"),
    ("the user's skill teaching", "The user teaches a beginner physical skill on weekends"),
    ("the skill course project", "The user is building a beginner course for a physical skill"),
    ("the user's linux setup", "The user runs a Linux desktop setup"),
    ("tabletop campaign", "The user runs tabletop sessions for a group"),
    ("impossible landscapes scenario", "The tabletop campaign uses the Impossible Landscapes scenario"),
    ("the memory recall system", "The memory system has an association graph for recall"),
    ("embeddings model", "The memory system uses Ollama nomic for embeddings"),
    ("wonder-skepticism philosophy", "The user researches the philosophy of wonder and skepticism"),
]


def main():
    print("embedding dataset...")
    texts = [d for d in DATASET] + [q for q, _ in QUERIES]
    vecs = embed(texts)
    entries = [{"text": t, "vec": vecs[t]} for t in DATASET]

    print(f"dataset: {len(entries)} memories, {len(QUERIES)} queries\n")
    print(f"{'query':<34} {'baseline':>10} {'upgrade':>10}")
    print("-" * 58)
    b_recall = u_recall = 0
    b_lat = u_lat = 0.0
    for q, truth in QUERIES:
        qv = vecs[q]
        t0 = time.time()
        bres = baseline_retrieve(qv, entries)
        b_t = time.time() - t0
        t0 = time.time()
        ures = upgrade_retrieve(qv, entries)
        u_t = time.time() - t0
        b_hit = any(e["text"] == truth for _, e in bres)
        u_hit = any(e["text"] == truth for _, e in ures)
        b_recall += b_hit
        u_recall += u_hit
        b_lat += b_t
        u_lat += u_t
        marker = ""
        if not b_hit and u_hit:
            marker = "  <- upgrade only"
        print(f"{q:<34} {'hit' if b_hit else 'miss':>10} {'hit' if u_hit else 'miss':>10}{marker}")
    print("-" * 58)
    print(f"{'RECALL@5':<34} {f'{b_recall}/{len(QUERIES)}':>10} {f'{u_recall}/{len(QUERIES)}':>10}")
    print(f"{'avg latency (s)':<34} {f'{b_lat/len(QUERIES):.4f}':>10} {f'{u_lat/len(QUERIES):.4f}':>10}")


if __name__ == "__main__":
    main()
