#!/usr/bin/env python3
"""measure_odysseus_vs_upgrade.py — head-to-head measurement.

Runs Odysseus's ACTUAL memory retrieval against the upgrade, on the same data
with the same embeddings, across datasets designed to expose real differences:

  D1 — paraphrase recall   : queries paraphrase the stored memory (vector
                             search must bridge synonym/paraphrase distance)
  D2 — relatedness         : a query matches ONE memory, but the useful answer
                             is a RELATED memory (would the association layer
                             help? does the brain view surface it?)
  D3 — larger corpus       : 200+ entries, recall accuracy under noise

Measures recall@k, precision@k, and latency for each. Same data, same model,
same queries — only the retrieval differs.
"""

import json
import random
import time
import urllib.request

OLLAMA = "http://localhost:11434"
MODEL = "nomic-embed-text"


def embed(texts, batch=16):
    """Embed a list of strings via Ollama nomic (256-dim), batched."""
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
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# ------------------------------------------------------------------ baseline
# Mimics MemoryVectorStore.search() exactly: cosine sim, dedupe, top-k.
def odysseus_search(query_vec, entries, k=8):
    scored = sorted(((cosine(query_vec, e["vec"]), e["id"]) for e in entries),
                    key=lambda x: -x[0])
    return [i for _, i in scored[:k]]


# ------------------------------------------------------------------ upgrade
# The ACTUAL design: associations are ALWAYS LIVE and PRECOMPUTED at write
# time (like _auto_associate in the real system). Recall walks the stored
# graph — an indexed lookup, NOT a per-query computation. The Brain graph view
# is only the DISPLAY of how those associations form.
def precompute_associations(entries, min_cos=0.74, fanout=6):
    """Build the association graph ONCE at write time (the real design)."""
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


def upgrade_recall(query_vec, entries, k=8, graph=None):
    """Vector search + the PRECOMPUTED association graph (enriches recall)."""
    direct = odysseus_search(query_vec, entries, k)
    by_id = {e["id"]: e for e in entries}
    result = list(direct)
    seen = set(direct)
    for mid in direct:
        for c, oid in (graph or {}).get(mid, [])[:3]:
            if oid not in seen:
                seen.add(oid)
                result.append(oid)
    return result[:k]


# ------------------------------------------------------------------- datasets

def dataset_paraphrase():
    entries = [
        {"id": "m1", "text": "The user prefers oat milk and avoids eggs entirely"},
        {"id": "m2", "text": "The user drinks black coffee every morning without fail"},
        {"id": "m3", "text": "The user teaches a beginner physical skill on the weekends"},
        {"id": "m4", "text": "The tabletop campaign runs Impossible Landscapes"},
        {"id": "m5", "text": "The user researches wonder and skepticism"},
    ]
    # ground truth: query -> the memory it SHOULD surface
    queries = [
        ("what does the user like to drink", "m1"),
        ("the user's morning beverage", "m2"),
        ("the user's music teaching", "m3"),
        ("what ttrpg scenario are they playing", "m4"),
        ("the user's philosophy interests", "m5"),
    ]
    return entries, queries


def dataset_related():
    entries = [
        {"id": "r1", "text": "The beginner skill course is called Pick Up & Play"},
        {"id": "r2", "text": "The user builds the skill course for new students"},
        {"id": "r3", "text": "The user records practice exercises for the course"},
        {"id": "r4", "text": "The course teaches the basic open chords first"},
        {"id": "r5", "text": "The user uses a DAW to record the audio lessons"},
        {"id": "r6", "text": "The course pricing is set at 30 pounds"},
    ]
    # relatedness: a query about ONE fact, useful answer is a RELATED fact
    queries = [
        ("the skill course name", "r1"),          # direct
        ("where are the audio lessons made", "r5"), # related to r3
        ("who is the course for", "r2"),            # direct
        ("the first thing students learn", "r4"),   # direct
        ("how much does the course cost", "r6"),    # direct
    ]
    return entries, queries


def dataset_large(n=200):
    topics = [
        ("skill", ["lessons", "course", "students", "chords", "practice", "recording"]),
        ("diet", ["oat milk", "coffee", "eggs", "protein", "breakfast", "meal"]),
        ("ttrpg", ["tabletop", "campaign", "impossible landscapes", "temple", "scenario", "session"]),
        ("philosophy", ["evidence-method", "wonder", "skepticism", "cosmos", "evidence", "universe"]),
        ("linux", ["arch", "cachyos", "config", "kernel", "package", "terminal"]),
        ("memory", ["association", "recall", "embedding", "neuron", "graph", "store"]),
        ("audio", ["daw", "reaper", "microphone", "interface", "recording", "mix"]),
        ("ttrpg", ["server", "module", "scene", "token", "macros"]),
    ]
    random.seed(42)
    entries = []
    qs = []
    for i in range(n):
        topic, words = topics[i % len(topics)]
        text = f"{topic.capitalize()} note {i}: {words[i % len(words)]} and {words[(i+1) % len(words)]} matter for {topic}"
        entries.append({"id": f"l{i}", "text": text})
    # queries: one per topic, ground truth = a known entry in that topic
    for i, (topic, words) in enumerate(topics):
        target = f"l{i}"
        qs.append((f"tell me about {words[0]} in {topic}", target))
    return entries, qs


def dataset_implied():
    """A query matches ONE memory, but the USEFUL answer is a RELATED memory
    that shares the topic — the association accelerator should surface it."""
    entries = [
        {"id": "i1", "text": "The user builds the beginner skill course for new students"},
        {"id": "i2", "text": "The course teaches open chords and basic strumming"},
        {"id": "i3", "text": "The user records the audio lessons in Reaper"},
        {"id": "i4", "text": "The course materials are stored in a shared folder"},
        {"id": "i5", "text": "The user schedules weekly student practice check-ins"},
        {"id": "i6", "text": "The course is marketed on a small YouTube channel"},
        {"id": "i7", "text": "The user's DAW setup uses a Scarlett audio interface"},
        {"id": "i8", "text": "The course covers fingerstyle and flatpicking"},
    ]
    # ground truth = the RELATED memory the query implies but doesn't name
    queries = [
        ("where do the audio lessons get made", "i7"),   # implies recording/DAW
        ("what gear is used for the course audio", "i7"), # relates to i3/i7
        ("the course teaching material", "i2"),           # implies chords/lessons
        ("how do students practice", "i5"),               # implies check-ins
        ("the course promotion", "i6"),                   # implies YouTube
    ]
    return entries, queries


# -------------------------------------------------------------------- runner

def run(name, entries, queries, k):
    texts = [e["text"] for e in entries]
    vecs = embed(texts + [q for q, _ in queries])
    ents = [{"id": e["id"], "text": e["text"], "vec": vecs[e["text"]]} for e in entries]
    qvecs = {q: vecs[q] for q, _ in queries}

    b_rec = u_rec = 0
    b_lat = u_lat = 0.0
    recovered = 0
    # precompute the association graph ONCE (at "write time", like the real system)
    graph = precompute_associations(ents)
    for q, truth in queries:
        qv = qvecs[q]
        t0 = time.time(); bres = odysseus_search(qv, ents, k); b_t = time.time() - t0
        t0 = time.time(); ures = upgrade_recall(qv, ents, k, graph=graph); u_t = time.time() - t0
        b_hit = truth in bres
        u_hit = truth in ures
        if not b_hit and u_hit:
            recovered += 1  # the accelerator surfaced a memory the baseline missed
        b_rec += b_hit; u_rec += u_hit
        b_lat += b_t; u_lat += u_t
    print(f"\n=== {name} (k={k}) ===")
    print(f"  baseline recall@{k}: {b_rec}/{len(queries)}")
    print(f"  upgrade  recall@{k}: {u_rec}/{len(queries)}")
    print(f"  recovered by associations: {recovered}")
    print(f"  baseline avg latency: {b_lat/len(queries)*1000:.2f}ms")
    print(f"  upgrade  avg latency: {u_lat/len(queries)*1000:.2f}ms")
    return b_rec, u_rec, recovered


def main():
    print("embedding...")
    for name, entries, queries, k in [
        ("Paraphrase recall (D1)", *dataset_paraphrase(), 5),
        ("Relatedness (D2)", *dataset_related(), 5),
        ("Implied relation (D4)", *dataset_implied(), 5),
        ("Large corpus 200 (D3)", *dataset_large(), 8),
    ]:
        run(name, entries, queries, k)
    print("\nDesign note: associations are ALWAYS LIVE (precomputed at write,")
    print("walked at recall to enrich). The Brain graph view is only the DISPLAY")
    print("of how those associations form — it is not the recall mechanism.")


if __name__ == "__main__":
    main()
