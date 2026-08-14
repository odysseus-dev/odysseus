# Memory Platform — a complete, immutable agent-memory OS for Odysseus

A **full memory system** — storage, retrieval, personality, growth, safety,
knowledge extraction, and an immutability layer that keeps the core blocks
protected from drift. Ships as `memory_platform/` (the platform) plus a thin
adapter (`src/odysseus_adapter.py`) that attaches it to Odysseus additively —
nothing replaced.

## The premise

This memory system starts as a **blank slate**. It ships with no facts about
any user, no inherited identity, no pre-loaded preferences. As the user
interacts with the agent, the system **records facts** — each verified fact
becomes an entry in the hybrid store, and over time the store accumulates
what is genuinely true about the **relationship between the user and the
agent**: preferences, projects, values, how they like to work. That record
**informs the agent** — recall surfaces it in context, the persona forms from
it, and the agent becomes more attuned to the person it works with, through
use, not through hardcoding.

---

## What the platform is

| Layer | What it does | Where |
|---|---|---|
| **Hybrid store** | Dense (sqlite-vec) + BM25 (FTS5) fused by RRF, with abstention and a tamper-evident audit chain | `memory_store.py` |
| **Immutable core** | The five blocks (constitution / persona / human / operating / project) checksummed by a drift ledger; any change must be anchored or journaled | `drift-ledger.py` |
| **Integrity chain** | Worthiness gate on intake (what may enter memory), claim audit on output (no unsupported strong claims), epistemic verification of the applied method | `worthiness.py`, `claim_audit.py`, `epistemic_verify.py` |
| **Growth** | Socratic belief-revision (a conceded argument amends the rule), evidence-graded promotion to persona/identity, lexicon-managed coherence | `socratic.py`, `curator.py`, `lexicon_evolution.py` |
| **Sleep / consolidation** | Pressure-gated consolidation: merge near-duplicates, prune stale, promote used; every run writes an auditable receipt | `sleep-time.py` |
| **Warm neurons** | Routing that keeps topic continuity across turns (per-session priming, decay) | `warm_router.py` |
| **Graph memory** | Durable facts also become temporal-graph triples | `graph_memory.py` |
| **Research lens** | The evidence method applied to research: assess source, contextual relevance, query expansion | `research_lens.py` |
| **Canary suite** | Self-proving checks for the platform's core mechanisms: store recall + tamper-evidence, drift detect/anchor, worthiness, claim-audit, socratic coherence | `canary.sh` + `canary-manager.sh` |

---

## The immutability model

The five core blocks are the **anchor of the relationship**. The drift ledger
records their sha256 + byte length; each sleep/compaction run compares against
the anchor. Two signals are tracked:

- **Cumulative volume** — how far a block has moved from its anchor. Blocks
  near/over threshold without an anchored source are flagged.
- **Source type** — how each byte of change is justified. Legitimate growth
  carries a journaled operation (a user directive, a promotion, an ADD/UPDATE
  applied by the curator). Unexplained bulk is drift.

The constitution is the most constrained: it changes **only by explicit
directive** (a 5% volume limit). Growth belongs to the harness, not the
engine — when the model swaps, the core blocks and their drift bounds persist
untouched.

---

## Why Odysseus would want this

1. **A memory that grows the relationship, not just stores facts.** Every
   interaction records verified facts; the store accumulates what is true
   about the user and the agent together; recall injects that record into
   context. The agent becomes informed by the relationship through use.

2. **A memory that is protected.** Drift protection means the constitution,
   persona, and operating rules can't be silently corrupted by an update, a
   bad prompt, or a model swap. That is the difference between a memory and a
   mutable blob.

3. **Evidence-gated growth.** Persona/identity values form only on real
   grounding (a worthiness gate + evidence receipts), never by assertion.

4. **A brain view that shows the real structure.** The on-request graph
   renders actual stored entries grouped by their neuron cluster, linked by
   real associations, with a status strip showing firing neurons and
   consolidation pressure.

---

## How it attaches

Nothing is replaced. The adapter (`src/odysseus_adapter.py`) attaches the
platform to Odysseus's existing `MemoryManager` + `MemoryVectorStore`:

| Adapter point | What it does |
|---|---|
| Platform store boot | Opens the platform's own hybrid store (sqlite-vec + FTS5); the connection stays open for recall |
| Drift ledger + watchdog | Snapshots the five blocks on attach; a background thread re-checks them on a timer |
| Integrity chain (intake) | Worthiness gate runs on every memory write — `REJECT` entries are refused |
| Integrity chain (output) | Claim audit runs on chat finalize — unsupported strong claims are degraded |
| Hybrid recall | Wraps `MemoryVectorStore.search` with BM25 + RRF fusion |
| Socratic + sleep | Additive on `MemoryManager` (new `socratic` source, consolidation) |
| Brain view | New tab in the memory modal + `/api/memory-brain/*` |

The portability contract is env-resolved (`MEMORY_STORE_DB`, `MEMORY_MEMORY_DIR`,
`MEMORY_SCRIPTS_DIR`, `MEMORY_PYTHON`), so the same code runs anywhere.

---

## Verified

- **Canary suite 5/5** — store recall + abstention + tamper-evident audit chain,
  drift detect/anchor, worthiness gate, claim-audit, socratic coherence —
  verified passing with `canary-manager.sh` (self-proving by construction).
- **Enforced, not just attached** — the intake gate refuses `REJECT` entries
  at write time, the output gate degrades unsupported strong claims on chat
  finalize, and a drift watchdog thread re-checks the core blocks on a timer.
  All verified in the running app.
- **Platform store boots** with hybrid recall + abstention + tamper-evident
  audit chain (verified with sqlite-vec 0.1.9).
- **Drift ledger** snapshots all five blocks and confirms `drift_ok: True`.
- **Full adapter attach** confirmed in the running app:
  `platform_store, drift_ledger, drift_ok, worthiness, claim_audit,
  hybrid_recall, associations, socratic, sleep, auto_sleep, intake_gate,
  drift_watchdog, output_gate` — all True.
- **Blank-slate blocks** — the core blocks ship empty; a new user starts clean
  and the relationship record grows from their own interactions.

---

## Honest scope

- **Proven:** the store, drift protection, integrity chain, hybrid recall,
  socratic growth, sleep/consolidation, brain view, blank-slate blocks.
- **Measured:** hybrid recall beats vector-only on the test corpus (11 vs 9).
- **Not included:** personal project tooling, any user's real persona or
  facts, and any personal data — the submission is the platform, portable and
  clean by construction.
