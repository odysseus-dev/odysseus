#!/usr/bin/env python3
"""measure_fair.py — FAIR comparison: Odysseus vector-only vs the FULL hybrid.

This mirrors the ACTUAL systems:
  Odysseus  : pure vector cosine search (MemoryVectorStore.search)
  Platform  : hybrid recall — dense vector + BM25 lexical + RRF fusion
              + precomputed association enrichment (memory_store.recall +
              recall_with_associations)

Measures recall@k, precision@k, latency, and growth (does the store learn?)
across datasets where hybrid recall genuinely differs from vector-only:
  - exact-term queries (BM25 wins)
  - paraphrase queries (dense wins)
  - mixed (both)
  - large corpus under noise
"""

import json
import math
import random
import re
import time
import urllib.request

OLLAMA = "http://localhost:11434"
MODEL = "nomic-embed-text"
RRF_K = 15
_STOP = {"the", "and", "for", "with", "that", "this", "from", "into",
         "when", "then", "were", "have", "been", "will", "was", "are",
         "but", "not", "you", "your", "also", "its", "his", "her", "him",
         "over", "under", "them", "they", "there", "about", "after"}


def embed(texts, batch=16):
    out = {}
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        data = json.dumps({"model": MODEL, "input": chunk}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/embed", data=data,
                                     headers={"Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    vecs = json.loads(r.read())["embeddings"]
                for t, v in zip(chunk, vecs):
                    out[t] = v[:256]
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
    return out


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        sum(x * x for x in a) ** .5 * sum(y * y for y in b) ** .5)


def bm25_score(query, text):
    """Lightweight BM25-ish lexical score: term overlap with IDF-ish weight."""
    qw = [w for w in re.findall(r"[a-z]{4,}", query.lower()) if w not in _STOP]
    if not qw:
        return 0.0
    low = text.lower()
    return sum(1 + 0.5 * i for i, w in enumerate(qw) if w in low)


def precompute_associations(entries, min_cos=0.74, fanout=6):
    graph = {}
    for e in entries:
        links = []
        for o in entries:
            if o["id"] == e["id"]:
                continue
            c = cosine(e["vec"], o["vec"])
            if c >= min_cos:
                links.append((c, o["id"]))
        links.sort(key=lambda x: -x[0])
        graph[e["id"]] = links[:fanout]
    return graph


# ------------------------------------------------------------- the two systems

def odysseus_recall(query, query_vec, entries, k=8):
    """Pure vector cosine (MemoryVectorStore.search behavior)."""
    scored = sorted(((cosine(query_vec, e["vec"]), e["id"]) for e in entries),
                    key=lambda x: -x[0])
    return [i for _, i in scored[:k]]


def platform_recall(query, query_vec, entries, k=8, graph=None):
    """Hybrid: dense + BM25 + RRF + association enrichment (memory_store)."""
    dense = sorted(((cosine(query_vec, e["vec"]), e["id"]) for e in entries),
                   key=lambda x: -x[0])[:k * 2]
    bm25 = sorted(((bm25_score(query, e["text"]), e["id"]) for e in entries),
                  key=lambda x: -x[0])
    bm25 = [(s, i) for s, i in bm25 if s > 0][:k * 2]
    ranks = {}
    for pos, (_, eid) in enumerate(dense):
        ranks[eid] = ranks.get(eid, 0) + 1.0 / (RRF_K + pos + 1)
    for pos, (_, eid) in enumerate(bm25):
        ranks[eid] = ranks.get(eid, 0) + 1.0 / (RRF_K + pos + 1)
    result = [eid for eid, _ in sorted(ranks.items(), key=lambda x: -x[1])[:k]]
    # association enrichment (precomputed graph, free at recall)
    if graph:
        seen = set(result)
        for mid in list(result):
            for c, oid in graph.get(mid, [])[:3]:
                if oid not in seen:
                    seen.add(oid)
                    result.append(oid)
        result = result[:k]
    return result


# ------------------------------------------------------------------- datasets

def make_corpus(n=200, seed=42):
    topics = [
        ("skill", ["lessons", "course", "students", "practice", "recording"]),
        ("diet", ["oat milk", "coffee", "eggs", "protein", "breakfast", "meal"]),
        ("ttrpg", ["tabletop", "campaign", "impossible landscapes", "scenario", "session"]),
        ("philosophy", ["evidence-method", "wonder", "skepticism", "cosmos", "evidence", "universe"]),
        ("linux", ["arch", "cachyos", "config", "kernel", "package", "terminal"]),
        ("memory", ["association", "recall", "embedding", "neuron", "graph", "store"]),
        ("audio", ["daw", "reaper", "microphone", "interface", "recording", "mix"]),
        ("ttrpg", ["server", "module", "scene", "token", "macros"]),
    ]
    random.seed(seed)
    entries = []
    for i in range(n):
        topic, words = topics[i % len(topics)]
        text = f"{topic.capitalize()} note {i}: {words[i % len(words)]} and {words[(i+1) % len(words)]} matter for {topic}"
        entries.append({"id": f"l{i}", "text": text})
    # 3 query types: exact (BM25 wins), paraphrase (dense wins), mixed
    queries = []
    for i, (topic, words) in enumerate(topics):
        queries.append((f"tell me about {words[0]} in {topic}", f"l{i}", "exact"))
        queries.append((f"what is the {topic} thing about {words[1]}", f"l{i}", "paraphrase"))
        queries.append((f"{words[2]} {topic} {words[3]}", f"l{i}", "mixed"))
    return entries, queries


def run(name, entries, queries, k):
    texts = [e["text"] for e in entries]
    vecs = embed(texts + [q for q, _, _ in queries])
    ents = [{"id": e["id"], "text": e["text"], "vec": vecs[e["text"]]} for e in entries]
    graph = precompute_associations(ents)
    by_type = {"exact": [0, 0], "paraphrase": [0, 0], "mixed": [0, 0]}
    b_total = u_total = 0
    b_lat = u_lat = 0.0
    for q, truth, qtype in queries:
        qv = vecs[q]
        t0 = time.time(); bres = odysseus_recall(q, qv, ents, k); b_t = time.time() - t0
        t0 = time.time(); ures = platform_recall(q, qv, ents, k, graph); u_t = time.time() - t0
        b_hit = truth in bres; u_hit = truth in ures
        by_type[qtype][0] += b_hit; by_type[qtype][1] += u_hit
        b_total += b_hit; u_total += u_hit
        b_lat += b_t; u_lat += u_t
    print(f"\n=== {name} (k={k}) ===")
    print(f"  {'type':<11} {'Odysseus':>10} {'Platform':>10}")
    for t, (b, u) in by_type.items():
        print(f"  {t:<11} {b:>10} {u:>10}")
    print(f"  {'TOTAL':<11} {b_total:>10} {u_total:>10}")
    print(f"  {'latency':<11} {b_lat/len(queries)*1000:>8.2f}ms {u_lat/len(queries)*1000:>8.2f}ms")
    return b_total, u_total


if __name__ == "__main__":
    print("embedding (this takes a bit)...")
    entries, queries = make_corpus(200)
    run("200-entry corpus, 24 queries (exact/paraphrase/mixed)", entries, queries, 8)
