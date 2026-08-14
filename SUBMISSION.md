# Memory Platform — submission metadata

## Repo name
`memory-platform`

## Tagline
"A complete, immutable agent-memory OS for Odysseus — hybrid store, drift-protected core blocks, an evidence-gated growth loop, and a brain view — attached additively to the existing MemoryManager + MemoryVectorStore."

## Topics
`odysseus` `memory` `agent-memory` `hybrid-recall` `immutability` `drift-protection` `socratic-method` `belief-revision` `sqlite-vec` `persona`

## Badges
- License: MIT
- Local-only (no cloud)
- Anonymous example persona (no attribution, no personal data)

---

## The work, in plain terms

**What it is:** a complete agent-memory platform, packaged so Odysseus can
adopt it incrementally. The platform ships in `memory_platform/` (38 modules);
the adapter (`src/odysseus_adapter.py`) attaches it to Odysseus additively —
nothing replaced.

**The problem it solves:** a memory that only accumulates is a ledger, not a
brain. This platform adds the three things a memory needs to *grow*:

1. **Revision** — a Socratic belief-revision loop: a conceded argument must
   amend the actionable rule or record a principled hold, audited.
2. **Protection** — the five core blocks (constitution / persona / human /
   operating / project) are checksummed by a drift ledger; any change must be
   anchored or journaled. Growth belongs to the harness, not the engine.
3. **Integrity** — an evidence-gated growth loop (worthiness gate on intake,
   claim audit on output) so persona/identity form only on real grounding.

**The example persona is anonymous.** The reasoning method (baloney detection,
wonder-skepticism, evidence-gated identity) is demonstrated without any source
attribution or traceable origin — a defensible example, not a real identity.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  ODYSSEUS (existing)                                        │
│  MemoryManager (src/memory.py)                              │
│  MemoryVectorStore (src/memory_vector.py)                   │
└──────────────────────┬──────────────────────────────────────┘
                       │  adapter (src/odysseus_adapter.py)
┌──────────────────────▼──────────────────────────────────────┐
│  MEMORY PLATFORM (memory_platform/)                         │
│  memory_store   — hybrid store: sqlite-vec + FTS5 + RRF,    │
│                   abstention, tamper-evident audit chain    │
│  drift-ledger   — sha256 checksums of the 5 core blocks;    │
│                   change must be anchored/journaled         │
│  worthiness     — intake gate (what may enter memory)       │
│  claim_audit    — output gate (no unsupported strong claims)│
│  epistemic_*    — verify the evidence method is applied     │
│  socratic       — belief revision + coherence check         │
│  curator        — evidence-graded promotion to persona      │
│  sleep-time     — pressure-gated consolidation + receipts   │
│  warm_router    — per-session neuron priming                │
│  graph_memory   — durable facts as temporal-KG triples      │
│  research_lens  — evidence method applied to research       │
└─────────────────────────────────────────────────────────────┘
```

**Why it attaches naturally:** the same env-resolved path contract
(`MEMORY_STORE_DB`, `MEMORY_MEMORY_DIR`, `MEMORY_SCRIPTS_DIR`,
`MEMORY_PYTHON`), the same additive philosophy — it adds a platform, not a
parallel system.

---

## Why it's needed

1. **Memory should be protected.** The constitution and persona are the anchor
   of identity. Without drift protection, an update or model swap can silently
   corrupt who the agent is. With it, growth is journaled and unexplained bulk
   is flagged.
2. **Growth should be real, not simulated.** A conceded argument that changes
   nothing is fake learning. The Socratic layer makes revision actionable and
   audited; the coherence check makes the failure visible.
3. **Growth should be evidence-gated.** Identity forms only on grounded
   material, never by assertion — so the persona is earned, not invented.

---

## The author

A long-time user of self-hosted agent tooling who built this platform to make
agent memory *grow*, *revise*, and *stay protected* rather than merely store.
The submission is the platform itself — portable, clean, and anonymous by
construction.
