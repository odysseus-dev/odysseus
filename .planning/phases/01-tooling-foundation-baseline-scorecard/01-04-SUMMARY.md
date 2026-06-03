---
phase: 01-tooling-foundation-baseline-scorecard
plan: 04
subsystem: testing
tags: [scorecard, ratchet, ruff, bandit, pip-audit, coverage, mypy, ast, fastapi-routes, ci-gate]

# Dependency graph
requires:
  - phase: 01-01
    provides: hash-pinned requirements.lock + requirements-dev.lock generated in python:3.12-slim
  - phase: 01-02
    provides: ruff zero baseline (clean lint) consumed as ruff_findings.total == 0
  - phase: 01-03
    provides: mypy lenient zero baseline + coverage/bandit config skeletons in pyproject.toml
provides:
  - "scripts/scorecard.py — re-runnable JSON-first 7-metric generator with --write/--check (ratchet) modes"
  - ".planning/scorecard/baseline.json — source-of-truth baseline metrics + ratchet thresholds (BASE-01 / SC#2)"
  - ".planning/scorecard/SCORECARD.md — rendered human view of baseline.json"
  - ".planning/scorecard/security-triage.md — committed triage notes referenced by security_findings"
  - "authenticated-endpoint enumeration (420 routes, 406 authenticated) — input list for Phase 2 COV-03 / Phase 5 SEC-01"
affects: [phase-02-coverage, phase-05-security-audit, ci-scorecard-gate, every-phase-boundary]

# Tech tracking
tech-stack:
  added: [pytest-cov coverage instrumentation, bandit 1.9.4, pip-audit 2.10.0 (dev-only, requirements-dev.lock)]
  patterns:
    - "JSON-first metric document is source of truth; markdown is a rendered view (D-10)"
    - "Ratchet thresholds: absolutes (ruff=0, sec high/critical=0) encoded directly, relative metrics pinned to baseline (D-11)"
    - "Shell-out to mature tools, do not reimplement; AST for typed%, pathlib for LOC"
    - "Auth allow-list mirrored verbatim from app.py:162-194 (cannot import AUTH_ENABLED-gated symbols)"

key-files:
  created:
    - scripts/scorecard.py
    - .planning/scorecard/baseline.json
    - .planning/scorecard/SCORECARD.md
    - .planning/scorecard/security-triage.md
  modified: []

key-decisions:
  - "Baseline captured inside python:3.12-slim with locked deps (local Py3.14 lacks runtime deps; import app fails locally) — same env strategy as Plans 01-01/01-02"
  - "git SHA passed via SCORECARD_GIT_SHA env because python:3.12-slim has no git binary; _run() normalizes any missing binary to returncode 127"
  - "typed% via AST (tool-version-independent, Open Q3): typed = return annotation AND all non-self/cls args annotated"
  - "perf is a guardrail not a gate (A2): import-app timing + one TestClient GET /api/health; falls back to None on TestClient error"

patterns-established:
  - "Re-runnable JSON-first scorecard with --write (regenerate) / --check (CI ratchet, nonzero on regression)"
  - "Every metric tolerant of an absent tool (returncode 127) — measurement never crashes the generator"

requirements-completed: [BASE-01]

# Metrics
duration: 13min
completed: 2026-06-03
---

# Phase 1 Plan 04: Baseline Scorecard Generator Summary

**JSON-first `scripts/scorecard.py` measuring all 7 D-14 metrics by shelling out to ruff/bandit/pip-audit/pytest-cov and reading the FastAPI route table; `--write` captured the clean post-ruff/post-mypy baseline (max_module_loc 5174, typed 49.62%, ruff 0, coverage 45.36%, 0 high/critical security findings, 420 routes / 406 authenticated) and `--check` ratchet-catches regressions (verified: exit 0 clean, exit 1 on a seeded F401).**

## Performance

- **Duration:** 13 min
- **Started:** 2026-06-03T18:05:00Z
- **Completed:** 2026-06-03T18:18:20Z
- **Tasks:** 2
- **Files modified:** 4 created

## Accomplishments
- Built the only bespoke code in Phase 1: `scripts/scorecard.py`, a re-runnable JSON-first generator with `--write` and `--check` (D-11 ratchet) modes — ruff-clean, ruff-format-clean, and mypy-clean itself.
- Captured the clean baseline in `python:3.12-slim` with hash-locked deps: all 7 metrics populated in `baseline.json` (source of truth) and rendered to `SCORECARD.md` (D-10).
- Demonstrated the ratchet (SC#2): `--check` exits 0 against the clean baseline and exit 1 against a seeded real regression (transient unused import → ruff F401), then reverted to a clean tree.
- Emitted the authenticated-endpoint enumeration (420 routes, 406 authenticated, 14 public/exempt) mirroring app.py's inline allow-list exactly — the input list for Phase 2 COV-03 / Phase 5 SEC-01.

## Measured Baseline Values

| Metric | Value | Ratchet threshold |
|--------|-------|-------------------|
| max_module_loc | 5174 (`src/tool_implementations.py`) | max 5174 |
| typed_pct_overall | 0.4962 (1099/2215 defs) | min 0.4962 |
| ruff_findings.total | 0 | max 0 |
| coverage_overall_pct | 45.36 (563 files) | min 45.36 |
| security_high_critical | 0 | max 0 |
| perf | import_app mean 1.461s (5 runs, Py 3.12.13); /api/health 4.03ms | guardrail (not gated) |
| auth_endpoints | 420 routes (406 authenticated, 14 exempt) | — |

> NOTE: RESEARCH cited `4144` as an illustrative max_module_loc; the actual measured value is **5174** (`src/tool_implementations.py`). The threshold is pinned to the real measurement.

## Task Commits

1. **Task 1: Build scripts/scorecard.py (7-metric generator + write/check)** - `08e114a` (feat)
2. **Task 1 deviation: tolerate missing binaries; env override for git SHA** - `f90dff8` (fix, Rule 3)
3. **Task 2: Capture clean baseline + verify ratchet** - `45bf128` (feat)

## Files Created/Modified
- `scripts/scorecard.py` - JSON-first 7-metric generator; `--write` produces baseline.json + SCORECARD.md, `--check` ratchet-compares and exits nonzero on regression.
- `.planning/scorecard/baseline.json` - Source-of-truth document: schema_version 1, git_sha, tool_versions, 7 metrics, ratchet thresholds.
- `.planning/scorecard/SCORECARD.md` - Rendered human view of baseline.json.
- `.planning/scorecard/security-triage.md` - Triage-notes stub (bandit + pip-audit tables) referenced by security_findings.

## Decisions Made
- **Container baseline:** captured inside `python:3.12-slim` with `--require-hashes -r requirements.lock` + `requirements-dev.lock` (the local Python 3.14 host lacks runtime deps, so `import app` — needed for perf + auth_endpoints + coverage — fails locally). Mirrors the Plan 01-01/01-02 env strategy.
- **git SHA via env:** `python:3.12-slim` ships no `git` binary, so the host passes the real SHA via `SCORECARD_GIT_SHA`; `_git_sha()` prefers that env, then `git`, then `"unknown"`.
- **typed% via AST** (Open Q3): tool-version-independent — typed = has return annotation AND every non-self/cls arg annotated.
- **perf as guardrail (A2):** recorded but not a gate; key-path TestClient sample falls back to `None` if it errors.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Hardened subprocess + git SHA for the runtime container**
- **Found during:** Task 2 (running `--write` inside `python:3.12-slim`)
- **Issue:** `python:3.12-slim` has no `git` binary, so `_git_sha()` raised `FileNotFoundError` and crashed `--write` after all 7 metrics had computed. Any shelled-out tool could likewise be absent.
- **Fix:** `_run()` now normalizes `FileNotFoundError` to a synthetic `CompletedProcess(returncode=127)` so every caller treats "tool absent" as "tool failed" without try/except; `_git_sha()` reads `SCORECARD_GIT_SHA` env first (set by the host), then falls back to `git`, then `"unknown"`.
- **Files modified:** scripts/scorecard.py
- **Verification:** Re-ran `--write` in-container with `SCORECARD_GIT_SHA` set → exit 0, real host SHA recorded; ruff/ruff-format/mypy still clean.
- **Committed in:** `f90dff8`

---

**Total deviations:** 1 auto-fixed (1 blocking, Rule 3)
**Impact on plan:** Necessary to run the generator in the locked runtime container. No scope creep; the application is unchanged (generator only measures).

## Issues Encountered
- **MCP teardown noise:** importing `app` and using `TestClient` prints a `BaseExceptionGroup` / "Attempted to exit cancel scope in a different task" during interpreter shutdown (built-in MCP stdio servers cancelled on teardown). It is harmless — it occurs *after* "Application shutdown complete" and after all metrics finish (GET /api/health returned 200; perf + auth_endpoints computed fine). `--write`/`--check` both exit 0/expected. Noted, not a regression.
- **Pre-existing test failures:** the ~20 container/test-isolation failures (deferred-items.md) are reflected in the coverage baseline (45.36%) — the scorecard records reality rather than treating them as a blocker, exactly as the baseline should.
- **Generated coverage artifacts** (`coverage.json`, `.coverage`) produced by the in-container pytest run were removed from the working tree; both are already in `.gitignore`.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- BASE-01 / SC#2 satisfied: a machine-comparable baseline with ratchet thresholds exists in `.planning/scorecard/`, re-runnable via `python scripts/scorecard.py --write|--check`.
- The auth_endpoints enumeration (in baseline.json) is ready as the input list for Phase 2 COV-03 and Phase 5 SEC-01.
- The `--check` ratchet is ready to be wired into a CI `scorecard` job (Plan 05) to fail PRs on regression.
- T-04-01 mitigation in place: the auth allow-list is mirrored verbatim with an explicit "keep in sync with app.py:162-194" comment.

## Self-Check: PASSED

- Files verified present: scripts/scorecard.py, .planning/scorecard/baseline.json, .planning/scorecard/SCORECARD.md, .planning/scorecard/security-triage.md, 01-04-SUMMARY.md
- Commits verified present: 08e114a, f90dff8, 45bf128

---
*Phase: 01-tooling-foundation-baseline-scorecard*
*Completed: 2026-06-03*
