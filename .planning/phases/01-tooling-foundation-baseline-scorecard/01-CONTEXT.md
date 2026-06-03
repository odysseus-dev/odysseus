# Phase 1: Tooling Foundation & Baseline Scorecard - Context

**Gathered:** 2026-06-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Stand up the **measurement baseline + CI enforcement harness + dependency lockfile** before any refactoring begins — the instrumentation that makes every later phase verifiable. Delivers: a CI pipeline that gates every PR on ruff/format/mypy/pytest/bandit/pip-audit; an objective baseline scorecard with no-regression thresholds; a hash-pinned dependency lockfile; and removal of orphaned code from the two reverts on `main`.

**In scope:** BASE-01 (scorecard), BASE-02 (dead-code audit), TOOL-01 (ruff lint+format), TOOL-02 (mypy), TOOL-03 (lockfile), TOOL-04 (CI gates).
**Not in scope:** any behavior/API/UX change; god-file splits (Phase 3); coverage gap-fill (Phase 2); `datetime.utcnow()` replacement — TOOL-05 is Phase 3.

</domain>

<decisions>
## Implementation Decisions

### CI Gating Posture
- **D-01:** **Hard-block on a clean baseline from day 1.** Reach zero findings first (ruff auto-fix + targeted per-file ignores; mypy lenient global + overrides), then every gate hard-blocks merges. A deliberate violation must fail the PR check (ROADMAP SC#1). No warn-only ramp.
- **D-02:** **Parallel GitHub Actions jobs** — separate jobs for lint, format-check, mypy, pytest, bandit, pip-audit. Fast feedback; clear which gate failed.
- **D-03:** **Triggers: `pull_request` + `push` to `main`.** Keeps main's status truthful and catches direct pushes.
- **D-04:** **CI installs via `uv` from the committed lockfile** (`setup-uv` action). Consistent with the uv-generated lockfile decision.
- **D-05:** Build on the existing `.github/workflows/` (currently only `issue-description-check.yml` / `pr-description-check.yml`) — add a new quality-gate workflow alongside them, do not modify the doc-check workflows.

### Lockfile Strategy
- **D-06:** **Single unified core lockfile.** Resolve the full core dependency set together (one source of truth, guaranteed mutually-compatible). The chromadb+fastembed+onnxruntime group (top transitive-conflict risk, Pitfall 10) is resolved inside this unified lock. **Fallback:** only split into a separate `requirements-ml` group if unified resolution fails to converge in `python:3.12-slim`.
- **D-07:** **Compile from a new top-level `requirements.in`** (direct deps only) → fully-pinned `requirements.lock`. pip-tools-style separation of intent from resolution. Do not just freeze the existing loose `requirements.txt`.
- **D-08:** **Separate optional lock** for `requirements-optional.txt` (AGPL PyMuPDF quarantine) — preserves the MIT-core / AGPL-quarantine boundary per `ACKNOWLEDGMENTS.md`. CI installs the core lock by default.
- **D-09:** **`uv pip compile --generate-hashes`, generated inside Docker `python:3.12-slim`** so the resolve matches CI exactly (ROADMAP phase note). Strongest reproducibility + supply-chain guarantee. The success criterion is `pip install -r requirements.lock` succeeding on a second clean Linux env without `ResolutionImpossible` (ROADMAP SC#3).

### Scorecard
- **D-10:** **JSON is the source of truth; a markdown view is rendered from it.** JSON is diffable, ratchetable, and CI-comparable across phases; markdown is the human summary.
- **D-11:** **Relative no-regression ratchet.** Each metric's threshold = its baseline value; CI fails on regression (mypy-typed % drops, ruff findings rise, max module LOC grows, etc.). Improvement-only — fits the behavior-preserving, incremental milestone. (A small number of absolute targets the roadmap already implies — e.g. zero ruff findings at baseline — are fine, but the default is ratchet.)
- **D-12:** **Scripted + re-runnable generator** (e.g. `scripts/scorecard.py`) regenerates every metric on demand and in CI, so any phase can re-measure identically — no methodology drift. Reproducible by construction.
- **D-13:** **Location: `.planning/scorecard/`** (`baseline.json` + rendered `SCORECARD.md`), kept with planning artifacts, outside the shipped app tree.
- **D-14:** Metrics to capture (from ROADMAP SC#2): per-module line counts, mypy-typed %, ruff finding count, security findings list, per-file test-coverage map, startup/key-path perf benchmark, authenticated-endpoint enumeration list. (Exact metric schema finalized in planning.)

### Lint / Type Baseline (the exact "clean baseline" CI enforces)
- **D-15:** **ruff rule families: `E`, `F`, `I`, `W`, `UP` (pyupgrade), `B` (bugbear).** Catches real bugs + import order + py3.12 modernization without flooding a mature codebase. Expand later via the ratchet. No `select=["ALL"]`, no `preview=true` (ROADMAP SC#5).
- **D-16:** **`ruff format` rolled out whole-repo in one standalone commit**, recorded in `.git-blame-ignore-revs` so blame stays useful; CI then enforces `ruff format --check`. Honors TOOL-01's clean-baseline requirement.
- **D-17:** **mypy lenient global baseline:** no `disallow-untyped-defs`, `ignore_missing_imports` for untyped third-party (`mcp.*`, etc.), `check_untyped_defs` off. Zero errors day 1; tighten per-module via `[[tool.mypy.overrides]]` as files are typed in later phases (inverted strictness).
- **D-18:** **Atomic commit sequence** for the lint/format introduction: (1) add ruff config (no code change), (2) `ruff --fix` auto-fixes, (3) `ruff format`. Each isolated and revertable.
- **D-19:** **ruff is pinned to an explicit version** (ROADMAP SC#5). Default: `E501`/line-length lint off, matching the existing no-hard-column convention (CONVENTIONS.md) — the formatter governs layout, not a lint cap.

### Claude's Discretion (decided at planning with the defaults noted)
- **BASE-02 dead-code audit** of reverts `67b63e9` (prompt-bar resize) and `1f6c5ac` (Codex Agent integration). Default approach: diff each revert, locate any orphaned imports/config/stubs left behind, remove them; verify via `git grep` for removed symbols and `ruff check` finding no `F401`/`F811` (ROADMAP SC#4).
- **bandit / pip-audit** suppression conventions — default: curated `.bandit` config + documented inline triage for pip-audit findings; high-severity must be closed or triaged with rationale.
- **Exact scorecard JSON schema** and the perf-benchmark harness (what "startup/key-path" measures) — default: minimal reproducible timing in the generator script.
- **mypy override granularity** for god-files — default: per-module overrides only where the global baseline would otherwise error.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & criteria
- `.planning/ROADMAP.md` §"Phase 1" — goal, 5 success criteria, and the pre-planning code-read note (verify lockfile env matches CI `python:3.12-slim`; chromadb/fastembed/onnxruntime conflict risk).
- `.planning/REQUIREMENTS.md` — BASE-01, BASE-02, TOOL-01..TOOL-04 (full requirement text + governing behavior-preserving constraint).
- `.planning/PROJECT.md` — Core Value, Constraints, Key Decisions (audit-driven "done", extend-existing-conventions).

### Codebase ground truth
- `.planning/codebase/CONCERNS.md` — hot-spots: unpinned deps, the two reverts, encryption-key co-location, the WAL/busy_timeout source conflict (informs the scorecard's security-findings list).
- `.planning/codebase/STACK.md` — exact stack/dep landscape (Python 3.12, the ML dep group).
- `.planning/codebase/CONVENTIONS.md` — house style: no autoformatter today, no hard column limit, double-quoted strings, f-string logging, atomic-IO rule. Constrains ruff config (D-19) and format rollout (D-16).
- `.planning/research/PITFALLS.md` §"Pitfall 10" — chromadb/fastembed/onnxruntime transitive-conflict risk driving D-06/D-09.
- `requirements.txt`, `requirements-optional.txt` — lockfile inputs (D-07/D-08).
- `pyproject.toml` — currently pytest-only; ruff/mypy/bandit/coverage config lands here.
- `ACKNOWLEDGMENTS.md` — MIT-core / AGPL-PyMuPDF quarantine rationale behind the separate optional lock (D-08).
- `CONTRIBUTING.md` — references `py_compile` / `node --check` as the only current mechanical checks; CI gates supersede these.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `.github/workflows/` already exists with two doc-check workflows — add the quality-gate workflow alongside; reuse the repo's existing Actions setup conventions.
- `pyproject.toml` already centralizes tool config (pytest) — ruff/mypy/bandit/coverage config extends this single file rather than introducing scattered config files.
- `uv` 0.11.18 is available locally (and is the chosen CI installer) — `pip-compile` is NOT installed, confirming `uv pip compile` as the lockfile tool.

### Established Patterns
- **Extend, don't replace** (PROJECT.md): the codebase favors a single `pyproject.toml`, atomic/standalone commits, and graceful additive change. The atomic config→fix→format commit sequence (D-18) and the blame-ignore-revs convention (D-16) match this.
- **Behavior-preserving contract**: the 355-file pytest suite is the "nothing broke" signal — `ruff format` and `--fix` commits must keep the suite green at each step.

### Integration Points
- New CI workflow consumes the committed lockfile (D-04/D-09) and runs the scorecard generator (D-12) so later phases can diff against `.planning/scorecard/baseline.json`.
- The scorecard's authenticated-endpoint enumeration (ROADMAP SC#2) is the input list Phase 2 COV-03 and Phase 5 SEC-01 build cross-owner isolation tests against.

</code_context>

<specifics>
## Specific Ideas

- Lockfile generation MUST happen inside Docker `python:3.12-slim` (the CI base image), not on the local Python 3.14 host — local-vs-CI resolution drift is the explicit risk called out in the ROADMAP phase note.
- The scorecard is intended as a living, machine-comparable artifact reused at every phase boundary, not a one-time snapshot — hence JSON-first + scripted generation.

</specifics>

<deferred>
## Deferred Ideas

- **TOOL-05** (`datetime.utcnow()` replacement across 24 files) — Phase 3, traced with downstream consumers (CalDAV `UNTIL`, scheduler cron comparisons).
- **FE-02** (eslint + prettier for `static/`, gated in CI) — v2 (REQUIREMENTS.md), requires verifying Docker Node ≥ 20.19.
- **LOG-01** (structured logging) and **EXC-01** (broad-except narrowing beyond DB paths) — v2.
- Broadening the ruff rule set beyond E/F/I/W/UP/B — deferred to the no-regression ratchet in later phases rather than front-loaded here.

None of these expanded the Phase 1 scope — discussion stayed within the tooling-foundation boundary.

</deferred>

---

*Phase: 1-Tooling Foundation & Baseline Scorecard*
*Context gathered: 2026-06-03*
