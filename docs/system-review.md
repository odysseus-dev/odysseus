# System Review — agent-memory platform (2026-08-15 overnight cycle)

Scope: `/home/nick/odysseus/memory_platform/` (source of truth), compared against
`/home/nick/.config/opencode/scripts/` (deployed), `memory-plugin.ts`, `sleep-time.py`,
`bootstrap.py`, and `/home/nick/odysseus/tests/`.

---

## 1. Inventory of every .py in memory_platform/ (source of truth)

| Module | Lines | What it does | Wired into plugin | Wired into sleep-time | Tests |
|---|---|---|---|---|---|
| `memory_store.py` | 1430 | Hybrid store core: sqlite-vec dense + FTS5 BM25, RRF fusion, chunks, entries, audit chain, auto-taxonomy hook | ✅ core (`storePy()`) | ✅ | `test_memory_lifecycle` (indirect) |
| `curator.py` | 1481 | Memory curation: intake, grading, entity extraction, dedupe, associations | ❌ | ✅ | none |
| `warm_router.py` | 815 | Warm-neuron router (keyword + priming + graph context) | ✅ warm tier | ❌ | none |
| `persona_gate.py` | 533 | Deterministic persona authority rail (user rules adjudicate requests) | ✅ `persona_gate` | ❌ | `test_persona_gate` |
| `deep_research.py` | 439 | Free, no-billing scholarly research (OpenAlex/S2/PubMed/arXiv/HAL/OpenAIRE/DOAJ), recency-ranked, `--since` floor, research_lens baloney filter | ❌ **GAP** | ❌ | none (existing `test_deep_research_*` target the Odysseus app's `src/deep_research`, not this) |
| `philosophy_kb.py` | 426 | Encoded philosophical KB (positions, fallacies, biases) | ❌ | ❌ | none |
| `worthiness.py` | 411 | Constitution-grounded intake filter (REJECT/ABSORB/PROMOTE) | ❌ | via curator | `test_memory_lifecycle` |
| `authority_harness.py` | 367 | Persona-authority test loop for model control | ✅ `model_probe`, limits | ✅ | `test_authority_harness` |
| `graph_memory.py` | 351 | Concept-mediated temporal knowledge graph | ❌ (via warm_router subprocess `graph_context`) | ❌ | none |
| `drift-ledger.py` | 351 | Drift protection for long projects (bulk-change detection, anchors) | ✅ `check` | ❌ | `test_memory_lifecycle` |
| `taxonomy.py` | 324 | Automatic wing+subcategory classification, growth seeds new wings | ✅ `taxonomy` | ❌ | `test_taxonomy` |
| `dedupe_policy.py` | 281 | Dedupe + supersede policy with receipts | ❌ (via memory_store) | ❌ | none |
| `growth_delta.py` | 239 | Behavioural growth deltas (single high-confidence gate, fast loop) | ✅ `recent` | ✅ | `test_growth_delta` |
| `lexicon_evolution.py` | 229 | Lexicon meta-judgement applied to evolution | ❌ (via curator) | ❌ | none |
| `socratic.py` | 202 | Actional belief-revision loop (record/amend/coherence) | ❌ | ❌ | `test_memory_lifecycle` |
| `grading.py` | 192 | Graded importance routing | ❌ (via curator) | ❌ | none |
| `skill_library.py` | 188 | Voyager-style skill library (observed→trusted→executable) | ✅ `skill_library` | ❌ | `test_skill_library` |
| `evidence_grade.py` | 163 | Evidence-quality grading | ❌ (via curator) | mention | none |
| `lessons.py` | 173 | Teachable-moments wing (Reflexion-style episodic buffer + behavioural delta) | ❌ **GAP** | ❌ | none |
| `aaak.py` | 158 | AAAK dialect compressor | ❌ (via curator/store/router) | ❌ | none |
| `persona_check.py` | 130 | "Am I still me?" persona-consistency self-check | ❌ | ❌ | none |
| `bootstrap.py` | 116 | Overnight growth cycle orchestration | n/a (standalone) | n/a | none |
| `consolidate.py` | 113 | Budget-aware consolidation pressure report | ❌ | ❌ | `test_memory_lifecycle` (pressure) |
| `persona_receipts.py` | 110 | Evidence receipts for identity/personality claims | ❌ | ✅ | none |
| `politics.py` | 92 | Politics wing: absorbed, evidence-tagged understanding | ✅ `politics` | ❌ | `test_politics` |
| `memory_env.py` | 87 | Portable path/env resolution (single source of all roots) | ✅ | ✅ | none |
| `_bridge.py` | 35 | Importable bridge for hyphen-named CLI modules | n/a | n/a | used by tests |
| `warm_neuron_store.py` | ~45 | Store-based warm neuron reader | ❌ (deployed-only consumers) | ❌ | none |

**Source modules that are orphans (present in source, referenced nowhere in source
or the plugin/sleep-time):** `philosophy_kb.py`, `socratic.py` (referenced only as a
`--source socratic` label string in the plugin, never invoked), `persona_check.py`
(consumer is the deployed-only `layered_informing.py`), `warm_neuron_store.py`
(consumers are deployed-only scripts). These are implemented wings that have not been
given a runtime surface — candidates for future wiring or explicit retirement.

---

## 2. Components that exist but are NOT wired into the plugin / sleep-time

### High-value gaps (built this cycle)

1. **`lessons.py` — teachable moments are not a plugin tool.**
   Only `bootstrap.py` touches it (`lessons.py recent` in step 1). The persona has no
   in-session way to `record` a mistake the moment it happens, or `recall` lessons
   before acting — so the "don't repeat it" check happens only at bootstrap time.
   **Action: wire as `memory_lessons` tool** (record / recall / recent), mirroring the
   `politics` tool pattern.

2. **`deep_research.py` — the free research tool is not a plugin tool.**
   Nothing in the plugin or sleep-time invokes it. It exists in source + deployed,
   fully functional, with `--since` recency support already implemented — but the
   persona cannot call it in-session. **Action: wire as `deep_research` tool** with
   `--since 2` support so research is always latest.

### Missing-from-source dependencies (build-adjacent)

3. **`research_lens.py` is imported by `deep_research.py` but missing from source.**
   It exists only in the deployed scripts dir. `deep_research.py` imports it in a
   try/except, so the failure is silent (verdicts degrade to UNASSESSED) — but the
   source of truth is incomplete. **Action: copy into source so the source is
   self-contained** (it is already deployed; keep in sync).

4. **`memory_compiler.py` is called by the plugin (`compileCore`) but missing from
   source.** The always-on core compilation is a plugin dependency that lives only in
   the deployed dir. Not built this cycle (deployed version is current); noted for the
   next sync so the source repo is a complete mirror of the running system.

### Other unwired components (documented, lower priority)

- `philosophy_kb.py`, `socratic.py`, `persona_check.py`, `warm_neuron_store.py` — see
  orphans above. No runtime surface; may be intentionally library-only.
- `bootstrap.py` is invoked manually/on the sleep agent (not by the plugin), which is
  by design.
- `consolidate.py` full run is not wired (bootstrap only runs `pressure`); the full
  consolidation cycle is deferred to sleep-time which calls `curator.py` directly.

---

## 3. Components missing tests

Existing platform tests in `/home/nick/odysseus/tests/`: `test_authority_harness`,
`test_growth_delta`, `test_memory_lifecycle`, `test_persona_gate`, `test_politics`,
`test_skill_library`, `test_taxonomy` — these mirror the private repo's `tests/` dir.

**No test coverage:**
- `lessons.py` — **built this cycle** (`test_lessons.py`)
- `deep_research.py` (the platform's own, not the app's) — **built this cycle**
  (`test_deep_research_platform.py`; the existing `test_deep_research_*.py` files test
  the Odysseus app's `src/deep_research.py`, a different module)
- `bootstrap.py`, `consolidate.py`, `curator.py`, `graph_memory.py`, `worthiness.py`,
  `evidence_grade.py`, `grading.py`, `dedupe_policy.py`, `aaak.py`,
  `lexicon_evolution.py`, `persona_check.py`, `philosophy_kb.py`, `persona_receipts.py`,
  `warm_neuron_store.py`, `warm_router.py`, `memory_env.py`

---

## 4. Dead code, stale references, broken imports

1. **Broken import (silent):** `deep_research.py` → `research_lens` — module not in
   source (see above). Fixed this cycle by adding `research_lens.py` to source.
2. **Broken import (silent):** plugin `compileCore` → `memory_compiler.py` — not in
   source. Documented above.
3. **Drift — `warm_router.py` `PROTECTED_NEURONS`:** source has
   `{"persona-example", "method-core"}`; deployed (and the live store, which contains
   `persona-alfred` and `philosophy-sagan-core` neurons) has
   `{"persona-alfred", "philosophy-sagan-core"}`. **The deployed version is correct and
   matches the live store; source is stale.** If the source were deployed as-is, the
   protected character neurons would lose their top rank. Not silently overwritten —
   flagged for the next sync so the correct names migrate back to source.
4. **Drift — `curator.py` example text:** source is example-sanitised ("oat milk",
   "example project", "teaches the skill"); deployed carries the real local examples
   ("coconut milk", "guitar", "teaches guitar"). Comment-only difference; deployed is
   the live-tuned version. Flagged, no action needed.
5. **`memory_store.py` auto-taxonomy hook:** present in source (lines 345-355), absent
   in the deployed copy — **source is ahead here**; the deployed store does not yet
   auto-classify topic-less writes. Flagged so the next deploy picks it up.
6. **`bootstrap.py`:** source has an uncommitted change (`skill_library list` without
   `--json`); deployed already matches the modified source. Commit pending.
7. **Deployed-only scripts with no source counterpart** (legacy/adjacent, by design —
   not platform source): `assistant.py`, `claim_audit.py`, `epistemic_verify.py`,
   `compat_check.py`, `voice_hybrid.py`, `cite_trace.py`, `persona_definition.py`,
   `version.py`, `delivery_adapter.py`, `epistemic_probe.py`, `layered_informing.py`,
   `lexicon-reconcile.py`, `local_memory.py`, `migrate_blocks.py`, `source_registry.py`,
   `system_status.py`, `task_watchdog.py`, DG/voicebox/pdf tooling.

---

## 5. Wiring reference (what the plugin + sleep-time actually call)

Plugin (`memory-plugin.ts`) tools → scripts: `memory_store.py` (all store tools),
`warm_router.py` (warm tier), `memory_compiler.py` (compileCore), `compat_check.py`
(compat), `drift-ledger.py` (check), `voice_hybrid.py` (voice blend), `assistant.py`
(feedback/value), `growth_delta.py` (recent), `authority_harness.py` (probe/limits),
`skill_library.py`, `taxonomy.py`, `politics.py`, `persona_gate.py`, `claim_audit.py`,
`epistemic_verify.py`.

Sleep-time (`sleep-time.py`): `curator.py`, `persona_receipts.py`, `memory_store.py`,
`cite_trace.py`, `growth_delta.py`, `persona_definition.py`, `authority_harness.py`,
`version.py`.

Bootstrap (`bootstrap.py`): `lessons.py` (recent), `growth_delta.py` (reflect),
`taxonomy.py` (wings), `skill_library.py` (list), `consolidate.py` (pressure),
`authority_harness.py` (limits).

---

## 6. Summary of actions taken this cycle

| Gap | Action | Status |
|---|---|---|
| lessons.py not a plugin tool | wired as `memory_lessons` tool | done |
| deep_research.py not a plugin tool | wired as `deep_research` tool (with `--since`) | done |
| lessons.py untested | `test_lessons.py` added (8 tests) | done |
| platform deep_research.py untested | `test_deep_research_platform.py` added (7 tests) | done |
| research_lens.py missing from source | copied into source | done |
| research_lens calibration: scholarly abstracts REJECTed | deep_research now assesses papers with `source_type="primary"` (a paper is the primary source about its own system); hedged scientific prose no longer buried | done |
| lessons.py `recent()` non-deterministic ordering | added `rowid DESC` tiebreaker | done |
| test_politics.py isolation bug (wrote to real store in batch runs) | fixture now patches `ms.DB_PATH`/`STORE_DIR` directly | done |
| source/deploy drift (warm_router, curator, memory_store, bootstrap) | documented, flagged for sync | flagged |

**Test results (memory venv, platform components):**
`pytest tests/test_lessons.py test_deep_research_platform.py test_memory_lifecycle.py
test_growth_delta.py test_taxonomy.py test_persona_gate.py test_politics.py
test_skill_library.py test_authority_harness.py test_memory_imports.py`
→ **90 passed**. The broader app suite (`-k "lessons or deep_research or memory"`)
still has pre-existing collection/runtime errors under the memory venv (missing
`fastapi.testclient`, MCP `Server` API drift, `bs4`/`cryptography` absent from
the app-vs-memory env split) — those pass under `/home/nick/odysseus/venv` and
are unrelated to the platform. Dependencies added to the memory venv this cycle
to enable collection: `sqlalchemy`, `beautifulsoup4`, `cryptography`,
`python-dateutil`, `Markdown`, `pytest-asyncio`, `mcp`, `nh3`.
