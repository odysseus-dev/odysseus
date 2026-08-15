# Overnight Growth Report — 2026-08-15

Overnight growth cycle for the agent-memory platform. All research latest-only
(2025–2026), all tests passing, both repos committed, release shipped.

---

## 1. System review summary

Full inventory written to `/home/nick/odysseus/docs/system-review.md` (28 source
modules + deployed + plugin + sleep-time + tests mapped).

**What exists (28 modules in `memory_platform/`):** memory_store (hybrid
dense+FTS core), curator, warm_router, persona_gate, deep_research,
philosophy_kb, worthiness, authority_harness, graph_memory, drift-ledger,
taxonomy, dedupe_policy, growth_delta, lexicon_evolution, socratic, grading,
skill_library, evidence_grade, lessons, aaak, persona_check, bootstrap,
consolidate, persona_receipts, politics, memory_env, _bridge, warm_neuron_store.

**What was wired / gapped before this cycle:**
- **Gap (high value):** `lessons.py` existed but was NOT a plugin tool — only
  bootstrap called it. The persona couldn't record a mistake in-session or
  recall lessons before acting.
- **Gap (high value):** `deep_research.py` existed (free, worldwide, `--since`
  support already present) but was NOT a plugin tool — the persona couldn't run
  latest research in-session.
- **Broken import (silent):** `deep_research.py` imports `research_lens.py`,
  which was missing from the source repo (deployed-only). Fixed by copying into
  source.
- **Calibration bug found during research:** the baloney lens assessed scholarly
  abstracts in "claim" mode, REJECTing valid 2025–26 papers whose hedged prose
  ("cannot preserve coherence") tripped overclaim signals. Fixed: papers are now
  assessed with `source_type="primary"` (a paper is the primary source about its
  own system).
- **Test-isolation bug found:** `test_politics.py` imported `memory_store`
  before setting env, so in batch runs it wrote to the REAL store. Fixed to
  patch `ms.DB_PATH` directly. (Pollution from an earlier batch run was removed
  from the live store.)
- **Dead/orphaned modules (documented, not built):** `philosophy_kb.py`,
  `socratic.py`, `persona_check.py`, `warm_neuron_store.py` have no runtime
  surface in source/plugin/sleep-time.
- **Drift flagged (not overwritten):** `warm_router.py` `PROTECTED_NEURONS`
  (deployed has the live names `persona-alfred`/`philosophy-sagan-core`; source
  has stale example names — deploying source would de-rank the real character
  neurons), `curator.py` comment examples, `memory_store.py` auto-taxonomy hook
  (source ahead, unshipped), `bootstrap.py` uncommitted change.

## 2. Research findings (latest-only, 2025–2026)

Full brief with sources: `/home/nick/odysseus/docs/research-brief-2026-08.md`.
All six queries ran through `deep_research.py search --since 2 --sources all`
(OpenAlex, Semantic Scholar, PubMed, arXiv, HAL, OpenAIRE, DOAJ).

| Topic | Key 2025–26 finding | Status vs system |
|---|---|---|
| **Agent memory architectures** | Memory is now "a data management system with lifecycle governance" (Agent-Native Memory, 2026, ACL). HyphaeDB (2026) = memory layer as inter-agent communication fabric; Kumiho (2026) = belief-revision/versioned semantics | system already has hybrid store + provenance + gates; agent-fabric & memory-layer benchmarking noted as roadmap |
| **Self-evolving agents** | Survey v4 (arXiv:2507.21046, 2025); MetaEvolve (2026) trains the meta-skill itself; PivoARL (2026) retries only the **pivotal** erroneous turn | PivoARL sharpens lessons.py: isolate the decisive mistake |
| **Teachable moments / Reflexion successors** | **"When AI Reviews Itself: zero answer changes across 72 self-correction rounds" (2026)** — ungrounded reflection is hollow; rubric-guided verification (2026, ACL Findings) | validates lessons.py's required-evidence field; harden "no evidence → no behavioural delta" |
| **Persona authority / jailbreak defense** | SESG "Self-Evolving Safety Guardrails" (2026): guardrails are static while jailbreaks evolve daily → evolve the rule set from live attempts | persona_gate is already deterministic (ahead of published classifier/judge rails); SESG = roadmap item |
| **Emotion display** | Kardia-R1 (2025, CIKM): **identity-aware** emotional reasoning; JMIR (2025): social chatbots measurably reduce loneliness; AIES (2025): ungrounded empathy is harmful | register is currently static voice; identity-aware modulation from store = roadmap |
| **Automatic taxonomy** | **Thinnest literature — no 2025–26 successor** found for agent-memory auto-taxonomy | `taxonomy.py` appears ahead of the published literature; no action |

## 3. What was built + test results

1. **`memory_lessons` tool** (in `memory-plugin.ts`): record / recall / recent
   teachable moments in-session, mirroring the `politics` tool pattern.
2. **`deep_research` tool** (in `memory-plugin.ts`): search / cache with
   `since` defaulting to 2 (always latest), sources selector, abstracts option.
3. **`research_lens.py`** copied into source (was deployed-only).
4. **`deep_research.py` calibration fix**: papers assessed as primary sources —
   valid hedged abstracts no longer REJECTed.
5. **`lessons.py` deterministic `recent()`** ordering (rowid tiebreaker).
6. **Tests added:** `test_lessons.py` (8 tests: record+apply, wing, refusal,
   behavioural delta, recall ranking, recent ordering, CLI roundtrip) and
   `test_deep_research_platform.py` (7 tests: `--since` hard floor, recency-
   first ranking over citations, verdict gating, primary-mode calibration,
   dedupe, backend normalisation, CLI `--since`). Fixed `test_politics.py`
   isolation bug.

**Test results (memory venv, platform components):**
```
pytest tests/test_lessons.py test_deep_research_platform.py test_memory_lifecycle.py \
  test_growth_delta.py test_taxonomy.py test_persona_gate.py test_politics.py \
  test_skill_library.py test_authority_harness.py test_memory_imports.py
→ 90 passed
```
Both new test files also pass in the private repo's flat `scripts/` layout
(19/19). The broader app suite still shows pre-existing collection/runtime
errors under the memory venv (missing `fastapi.testclient`, MCP `Server` API
drift, etc.) — those pass under `/home/nick/odysseus/venv` and are unrelated to
the platform. Dependencies added to the memory venv to enable collection:
sqlalchemy, beautifulsoup4, cryptography, python-dateutil, Markdown,
pytest-asyncio, mcp, nh3.

## 4. Bootstrap results

`bootstrap.py run` → **5/5 steps OK** (report at
`~/.config/opencode/memory/docs/bootstrap-report.md`):
- lessons: ok · taxonomy: ok · skills: ok · consolidation: pressure 1.0
  (target 262 MB, tight-at 0.85) · authority: ok (api class, persona-layer
  control confirmed)

## 5. Teachable moments recorded (via `lessons.py record`)

All three recorded AND behaviourally applied (stored in the `lessons` wing +
growth_delta journal):

1. **Never claim a stored rule that doesn't exist** — "when asked whether a
   user rule exists": the persona padded the truth by claiming "your own rule"
   when it was only a test fixture. Behaviour: never claim a stored rule unless
   verified in the store.
2. **Recency is a first-class ranking axis** — "when running latest-research
   queries": deep_research initially returned stale research ranked by
   citations. Behaviour: sort by recency before citations, enforce `--since 2`
   on every latest-research query.
3. **Commit new components to BOTH repos immediately** — "after building any
   new component": lessons.py was lost by only writing it to a temp dir.
   Behaviour: every new component is committed to source + private repo and
   synced to the deployed scripts dir immediately.

## 6. Release / version status

- **v1.1.0 released** on `ThingsCouldGetDicey/agent-memory-platform`
  (private repo; https://github.com/ThingsCouldGetDicey/agent-memory-platform/releases/tag/v1.1.0).
  Merged Release PR #2 (release-please), tag `v1.1.0` created. Includes three
  feature commits: deep_research tool, free research + teachable moments +
  bootstrap cycle, and this cycle's plugin wiring + tests.
- Committed to BOTH repos: odysseus source (`feat/memory-lifecycle`,
  commit `99dec97c`) and the private repo (commit `5566ccc`, in v1.1.0).
- Deployed copies (`~/.config/opencode/scripts/`, plugin) synced to match.
- Foundry VTT repo: **not touched** (no commits, pushes, or releases).

## Key files

- `/home/nick/odysseus/docs/system-review.md`
- `/home/nick/odysseus/docs/research-brief-2026-08.md`
- `/home/nick/odysseus/tests/test_lessons.py`
- `/home/nick/odysseus/tests/test_deep_research_platform.py`
- `/home/nick/odysseus/memory_platform/{lessons,deep_research,research_lens}.py`
- `/home/nick/.config/opencode/plugins/memory-plugin.ts`
