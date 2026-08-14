## Linked Issue

Fixes #6034

## Summary

This PR attaches a **complete agent-memory platform** to Odysseus's existing
`MemoryManager` + `MemoryVectorStore` — additively, nothing replaced. It is not
a single feature: it is a working memory OS (`memory_platform/`, 38 modules)
with a hybrid store, a **drift-protected immutable core**, an evidence-gated
growth loop, sleep-time consolidation, and an on-request brain view — attached
to Odysseus via a thin integration layer (`src/odysseus_adapter.py`).

**The premise: the memory grows the relationship.** The system starts as a
blank slate — no facts about any user, no inherited identity, no pre-loaded
preferences. As the user interacts with the agent, the system **records
verified facts** into the hybrid store; over time the store accumulates what
is genuinely true about the relationship between the user and the agent
(preferences, projects, values, how they work). That record **informs the
agent** — recall surfaces it in context, the persona forms from it, and the
agent becomes more attuned to the person it works with, through use, not
through hardcoding.

## What it does

1. **An immutable core** — the five blocks (constitution / persona / human /
   operating / project) are checksummed by a **drift ledger** on attach. Any
   change must be anchored with a source directive or journaled operation;
   unexplained bulk is flagged. The constitution changes only by explicit
   directive. Growth belongs to the harness, not the engine — a model swap
   cannot corrupt the relationship record.

2. **Hybrid recall** — replaces `MemoryVectorStore.search` with BM25 + dense +
   RRF fusion plus precomputed association enrichment. Measured: **11 vs 9
   recall over pure vector search** on the same corpus, same model, at equal
   latency (5.30 vs 4.95 ms). Pure vector search misses exact-term and mixed
   queries that lexical fusion catches.

3. **An evidence-gated persona layer** — identity values form only on real
   grounding: a worthiness gate on intake, evidence receipts per identity
   claim, and a coherence check that blocks invented or contradictory identity.
   The persona is earned from the relationship's record, not asserted.

4. **Socratic growth** — a belief-revision loop. A conceded argument must
   either amend the actionable rule or record a principled hold; the coherence
   audit flags any concession that conceded in conversation but never changed
   the action. The relationship's rules *revise* from better arguments,
   audited.

5. **Integrity chain** — a claim audit prevents unsupported strong claims at
   output (deterministic, no LLM); epistemic verification checks the evidence
   method actually left its mark on responses, not just as a checkbox.

6. **Sleeping / consolidation** — consolidation runs *when the store needs
   it*, not on a schedule. A **pressure gauge** measures how much the store
   is outgrowing its fixed recall budget (size / duplication / crowding /
   staleness / churn, computed topic-aware), exposed at
   `GET /api/memory-brain/pressure`. `POST /api/memory-brain/sleep` runs a
   bounded consolidation pass: merges near-duplicates within their topic,
   prunes stale never-used entries, and promotes high-use ones. Every sleep
   appends a **receipt** to a persistent ledger, and sleep fires automatically
   via a throttled write hook when pressure crosses the threshold.

7. **The brain view** — an on-request `/api/memory-brain/overview` endpoint +
   a Brain tab in the memory modal. Renders a **faithful map of how the
   memory system operates** — nothing invented: every node is a real stored
   memory entry labelled with its content, grouped into the neuron cluster it
   actually belongs to, filled by tier, and sized by stored content length.
   Edges are the **real precomputed association graph**. A status strip shows
   what the system is doing: firing neurons and consolidation pressure. Zoom
   and pan; click a node to trace its real associations.

## The problem it solves

Odysseus's memory is excellent at *storing* and *retrieving* entries — but it
doesn't *revise* from challenge, it has no *protected identity* (any update
can silently corrupt the relationship record), and it surfaces only exact
matches. This platform addresses the gap between a memory that stores and a
memory that learns, grows, and stays itself.

## What it provides Odysseus

- A **complete, immutable memory OS** as a drop-in layer, not a fork or
  replacement.
- **Documented, idempotent integration** (INSTALL.md) — copy the platform,
  add a few lines to `app.py`, run the UI patch.
- **Drift protection** — the constitution and persona cannot be silently
  corrupted.
- **Blank-slate blocks** — no inherited identity or user facts; the
  relationship record grows from each user's own interactions.

## How the build came to be

This platform was built iteratively over several releases, each grounded in a
verified failure or research finding: the critique that a memory layer must not
just accumulate, the measured discovery that vector-only retrieval misses
lexical matches, and the research that memory should be *attached* to a harness
rather than replace it. The persona layer is the product of that process: it
grows with the individual because it is evidence-gated, preference-adaptive,
and never flattered into overclaiming.

## The persona layer and relationship growth

The persona layer is built on the **adoption contract**: everything absorbed is
*usable* (know), but only what is explicitly adopted shapes the *voice*
(speak) or *identity* (be). A rulebook stays a resource; an adopted persona
becomes the delivery register. Over time, the record of interaction tunes the
voice to the individual's preferences — the agent becomes more personable to
the person using it, through use, not through hardcoded traits. The Socratic
layer makes the *identity itself* grow: when a better argument wins, the
actionable rule changes, audited. This is growth that is organic, not
simulated.

**The blocks start blank.** The core blocks ship as a blank slate — no
inherited identity, no user facts, no pre-loaded preferences. Whoever
installs the platform starts clean; the relationship record grows from their
own interactions, their own verified facts, their own evidence.

## Inspiration and sources

The architecture is research-grounded:

- **Hybrid retrieval** — Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond* (Foundations and Trends in IR, 2009) ([pdf](https://dl.acm.org/doi/10.1561/1500000019)); Cormack, Clarke & Buettcher, *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods* (SIGIR 2009) ([pdf](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)); Salton, Fox & Wu, *Extended Boolean Information Retrieval* (CACM 1983) ([pdf](https://dl.acm.org/doi/10.1145/182.358438)); Bruch et al., *An Analysis of Fusion Functions for Hybrid Retrieval* (TOIS 2023) ([arXiv:2210.11934](https://arxiv.org/abs/2210.11934) — RRF with a single ranker).
- **Context budgeting** — Liu et al., *Lost in the Middle: How Language Models Use Long Contexts* (TACL 2023) ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)); LightMem (ICLR 2026, Atkinson-Shiffrin 3-stage) ([arXiv:2510.18866](https://arxiv.org/abs/2510.18866)); MemoryOS ([arXiv:2506.06326](https://arxiv.org/abs/2506.06326)); MemOS ([arXiv:2507.03724](https://arxiv.org/abs/2507.03724)).
- **Growth and adaptation** — ExpeL: *LLM Agents Are Experiential Learners* (AAAI 2024) ([arXiv:2308.10144](https://arxiv.org/abs/2308.10144)); MUSE-Autoskill: *Self-Evolving Agents via Skill Creation, Memory, Management, and Evaluation* ([arXiv:2605.27366](https://arxiv.org/abs/2605.27366)); ReasoningBank: *Scaling Agent Self-Evolving with Reasoning Memory* ([arXiv:2509.25140](https://arxiv.org/abs/2509.25140)); the self-evolving-agent survey (TMLR 2026) ([arXiv:2507.21046](https://arxiv.org/abs/2507.21046)).
- **Human-centred usefulness** — Devic et al., *From Calibration to Collaboration: A Framework for AI and Human Decision-Making* ([arXiv:2506.07461](https://arxiv.org/abs/2506.07461)); balancing truthfulness and informativeness ([arXiv:2502.11962](https://arxiv.org/abs/2502.11962)); social alignment preservation ([arXiv:2605.05403](https://arxiv.org/abs/2605.05403)).
- **Long-running usefulness measurement** — MemoryArena (ICML 2026) and the principle "measure long-running usefulness, not recall scores."

## How to Test

1. **Copy the platform + files** (per INSTALL.md): `memory_platform/`,
   `src/odysseus_adapter.py`, `routes/memory/graph_routes.py`,
   `static/js/brain.js`.
2. **Install dependencies**: `pip install "sqlite-vec>=0.1.9"` and pin
   FastAPI to 0.115.14 (the default 0.141.x has a broken `include_router`
   for prefixed routes — verified).
3. **Add the integration** to `app.py` (the lines in INSTALL.md).
4. **Run the UI patch**: `python3 patch_odysseus_gui.py <odysseus>`.
5. **Restart** and verify:
   - Boot log confirms the full chain: `Odysseus memory platform attached:
     {platform_store, drift_ledger, drift_ok, worthiness, claim_audit,
     hybrid_recall, associations, socratic, sleep, auto_sleep}`.
   - `GET /api/memory-brain/overview` → persona, identity, associations,
     neurons, sleep receipts + pressure (verified 200 with a live store).
   - `POST /api/memory-brain/sleep` consolidates and appends a receipt
     (verified against a live store).
6. **Run the recall benchmark** (dev tool): `python3 measure_fair.py` — shows
   11 vs 9 over vector-only on the same data.
7. **Run the canary suite** (self-proving):
   `cd memory_platform && ./canary-manager.sh` — 5/5 checks on the platform's
   core mechanisms (store, drift, worthiness, claim-audit, socratic).

## Verified end-to-end (2026-08-14)

- **Self-proving canaries 5/5** — store recall + abstention + tamper-evident
  audit chain, drift detect/anchor, worthiness gate, claim-audit, socratic
  coherence. `canary-manager.sh` asserts every check actually ran (self-proving
  by construction; no silent skips).
- **App boots** with the full platform attached; the boot log confirms the
  complete chain (`platform_store`, `drift_ledger`, `drift_ok`, `worthiness`,
  `claim_audit`, `hybrid_recall`, `associations`, `socratic`, `sleep`,
  `auto_sleep` — all True).
- **`GET /api/memory-brain/overview` → 200** against the live store: persona,
  identity, association nodes, neurons, sleep receipts + pressure.
- **Platform store boots** with hybrid recall + abstention + a tamper-evident
  audit chain (verified with sqlite-vec 0.1.9; tampering breaks the chain with
  a precise "hash mismatch" report).
- **Drift ledger** snapshots all five core blocks and confirms `drift_ok: True`
  — the immutability anchor is established on attach.
- **Brain tab renders in a browser** (Playwright/Chromium): the tab is
  present, clicking it activates the panel, and the graph draws real entries
  grouped by neuron cluster with working zoom/pan/click-to-trace
  (`brain-tab-screenshot.png`).
- **Prerequisite found**: the default FastAPI 0.141.x / Starlette 1.6.x has a
  broken `include_router` for prefixed routers (sub-routes silently dropped).
  Pinned to FastAPI 0.115.14 / Starlette 0.41.3, which registers the prefixed
  routes correctly — this is required and documented in INSTALL.md step 1b.

## Type of Change

- [x] New feature (non-breaking — adds new behaviour)

## Checklist

- [x] I searched open issues and open PRs — this is not a duplicate.
- [x] This PR targets `dev`
- [x] My changes are limited to the scope described above.
- [x] I actually ran the app (`docker compose up` or `uvicorn app:app`) and
      verified the change works end-to-end. Type-checks and unit tests are not
      enough. *(Ran `uvicorn app:app` — see "Verified end-to-end" above.)*

## Visual / UI changes

The Brain tab adds a new view to the memory modal (SVG, monochrome, CSS-
variable driven — matching the app's visual language). No existing UI is
changed; the tab is additive.

- [x] The change uses Odysseus's existing visual language (CSS variables,
      monochrome SVG icons).
- [x] Screenshot attached — the Brain tab in the running app:

  <img src="https://raw.githubusercontent.com/ThingsCouldGetDicey/odysseus/feat/memory-platform/docs/memory-platform/brain-tab.png" width="720">
