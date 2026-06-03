---
phase: 01-tooling-foundation-baseline-scorecard
plan: 02
subsystem: testing
tags: [ruff, lint, format, pyproject, git-blame-ignore-revs, code-quality, ci-gate]

# Dependency graph
requires:
  - phase: 01-01
    provides: "ruff==0.15.15 pinned in requirements-dev.lock; python:3.12-slim test image with locked deps (the behavior-contract runtime)"
provides:
  - "[tool.ruff] lint + format config in pyproject.toml (select=[E,F,I,W,UP,B], ignore=[E501,E402,B904,E712,E711,B008], quote-style=double, target=py312)"
  - "A genuine zero-finding ruff baseline: `ruff check .` exits 0, `ruff format --check .` exits 0"
  - ".git-blame-ignore-revs recording the bulk ruff-format commit SHA"
  - "Codebase-wide import-sort + pyupgrade + safe-fix normalization, behavior-preserved (pytest baseline unchanged)"
affects: [01-04 scorecard ratchet, 01-05 CI quality-gate, all later refactor phases]

# Tech tracking
tech-stack:
  added: []  # ruff binary already pinned in 01-01 dev lock; this plan adds config + baseline only
  patterns:
    - "Single-config-file: all ruff config in pyproject.toml (no ruff.toml)"
    - "D-18 three-commit atomic sequence: config-only -> --fix -> format, full pytest gate after each code-touching commit"
    - ".git-blame-ignore-revs for mechanical bulk reformat"
    - "Config-level per-file-ignores over scattered noqa for residuals"

key-files:
  created:
    - .git-blame-ignore-revs
  modified:
    - pyproject.toml
    - "500 .py source/test files (mechanical: import-sort, pyupgrade, formatting)"
    - tests/test_session_endpoint_owner_scope.py
    - tests/test_security_regressions.py
    - tests/test_cookbook_dependency_completion_regression.py

key-decisions:
  - "Skipped ruff --unsafe-fixes (optional per plan): E711/E712/F841 rewrites carry semantic-change risk (T-02-01); residuals handled via config instead"
  - "Zero baseline reached via top-level `ignore` for codes that conflict with DOCUMENTED idioms (E402 lazy imports, B904 degrade-gracefully, E712/E711 SQLAlchemy ==/IS NULL, B008 FastAPI Form/Depends) plus per-file-ignores for ~27 files with localized minor residuals — NOT a select-family change"
  - "src/database.py needs F401/F403 per-file-ignore: it is a re-export shim with no __all__; F401 deletes its load-bearing re-exports"
  - "tests/** needs F401/F811 per-file-ignore: tests use import-time side-effect imports (e.g. `import src.agent_tools` to prime the module cache and break a runtime circular import) that F401 would silently strip"

patterns-established:
  - "Behavior contract = the 355-file pytest suite run in python:3.12-slim with the locked deps (local Py3.14 env lacks runtime deps and cannot gate)"
  - "Purge __pycache__ before every container pytest run on a bind-mounted worktree — stale .pyc silently masks/poisons import-order bisection"

requirements-completed: [TOOL-01]

# Metrics
duration: 19min
completed: 2026-06-03
---

# Phase 01 Plan 02: ruff Lint + Format Adoption & Zero Baseline Summary

**Adopted ruff lint + format via pyproject.toml and brought the repo to a genuine zero-finding, stably-formatted baseline through the locked D-18 three-commit sequence (config -> --fix -> format), preserving the 355-file pytest behavior contract exactly (20 pre-existing failures unchanged, zero new failures).**

## Performance

- **Duration:** ~19 min
- **Started:** 2026-06-03T17:36:10Z
- **Completed:** 2026-06-03T17:55:30Z
- **Tasks:** 4
- **Files modified:** pyproject.toml + .git-blame-ignore-revs + 500 .py files (mechanical) + 3 test fixes

## Accomplishments

- `[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.format]` added below the existing pytest block (no ALL, no preview; E/F/I/W/UP/B selected; E501 off; quote-style double; target py312).
- Safe `ruff check . --fix` applied across 306 files (import-sort, pyupgrade typing/datetime rewrites, F401 cleanup): baseline **3548 -> 644** findings.
- Whole-repo `ruff format` (453 files) committed as one mechanical commit; SHA `1e2074f1f613ce1acdbc0f09b9b350602879c180` recorded in `.git-blame-ignore-revs`.
- Residuals driven to **zero**: `ruff check .` exits 0, `ruff check . --select F401,F811` exits 0 (also satisfies BASE-02 SC#4), `ruff format --check .` exits 0 (SC#1).
- Full pytest suite (python:3.12-slim + locked deps) green after every code-touching commit: **20 failed / 1737 passed / 82 skipped** — identical pre-existing baseline, **0 new failures, 0 collection errors**.

## Task Commits

1. **Task 1: Add [tool.ruff] config (D-18.1)** - `d419f84` (chore) — config-only; includes the two behavior-preserving per-file-ignores (`src/database.py` F401/F403, `tests/**` F401/F811) discovered during Task 2.
2. **Task 2: Safe ruff --fix (D-18.2)** - `81de772` (style) — 306 .py files, gated green.
3. **Task 3a: Whole-repo ruff format (D-18.3)** - `1e2074f` (style) — 453 files, purely mechanical.
3. **Task 3b: Record blame-ignore SHA** - `1b268c7` (chore) — `.git-blame-ignore-revs`.
3. **Task 3c: Fix brittle source-introspection tests** - `cbb208d` (test) — 3 tests loosened to tolerate format line-wrapping (separate from the mechanical format commit to keep blame accurate).
4. **Task 4: Drive residuals to zero** - `1555c8c` (chore) — config-only ignore + per-file-ignores; suite unchanged.

## Files Created/Modified

- `pyproject.toml` - ruff lint+format config, ignore list, per-file-ignores.
- `.git-blame-ignore-revs` - records the bulk format commit `1e2074f`.
- 500 `.py` files - mechanical import-sort / pyupgrade / formatting (no behavior change).
- `tests/test_session_endpoint_owner_scope.py`, `tests/test_security_regressions.py`, `tests/test_cookbook_dependency_completion_regression.py` - assertion loosening (see deviations).

## Decisions Made

- **No `--unsafe-fixes`.** Optional per plan; the unsafe set (E711/E712/F841/etc.) can change runtime semantics (T-02-01). Residuals were instead suppressed at config level where they reflect intentional idioms.
- **Zero-baseline via documented-idiom `ignore` + per-file-ignores**, not by narrowing `select`. The E/F/I/W/UP/B families remain selected; only codes that fight the codebase's documented patterns (lazy imports, degrade-gracefully `except`, SQLAlchemy `==`/`IS NULL`, FastAPI `Form/Depends` defaults) are globally ignored.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] ruff F401 deleted load-bearing re-exports in `src/database.py`**
- **Found during:** Task 2 (`--fix`)
- **Issue:** `src/database.py` is a pure re-export shim (`from core.database import *` + an explicit name list) with no `__all__`. F401 deleted all 34 explicit re-exports, breaking `from src.database import X` across the codebase.
- **Fix:** Added `"src/database.py" = ["F401", "F403"]` to `[tool.ruff.lint.per-file-ignores]`.
- **Verification:** Re-exports preserved; container pytest green.
- **Committed in:** `d419f84` (Task 1 config commit, amended).

**2. [Rule 3 - Blocking] ruff F401 stripped a side-effect import that breaks a runtime circular import**
- **Found during:** Task 2 (`--fix`) — surfaced as `ImportError: cannot import name 'parse_tool_blocks' from partially initialized module 'src.tool_parsing'` collecting `tests/test_unknown_tool_calls.py`.
- **Issue:** The test had `import src.agent_tools` purely for its import-time side effect — it primes the module cache so `agent_tools` fully initializes before the cyclic `tool_parsing <-> agent_tools` import resolves. F401 considered it "unused" and removed it (also removed `import pytest`), breaking collection.
- **Fix:** Added `"tests/**" = ["F401", "F811"]` to per-file-ignores (the plan's Task 4 recommendation).
- **Verification:** `tests/test_unknown_tool_calls.py` collects and passes (5 passed); full suite back to baseline.
- **Committed in:** `d419f84` (Task 1 config commit, amended).

**3. [Rule 1 - Bug] `ruff format` line-wrapping broke 3 source-introspection regression tests**
- **Found during:** Task 3 (`ruff format`)
- **Issue:** Three tests read route module source as text and asserted exact single-line function signatures / `re.search(...)` call layout. `ruff format` wrapped those long signatures and the regex call across lines, so the substring assertions failed (3 new failures: `test_diagnostics_routes_are_admin_gated`, `test_chat_endpoint_recovery_paths_are_owner_scoped`, `test_backend_status_treats_download_exit_zero_as_completed`). The route runtime behavior is unchanged — only source layout moved.
- **Fix:** Loosened the assertions to match the function name + the security-relevant token (owner-scoping param, `require_admin` count, regex literal) independently of single-vs-multi-line layout. Kept in a **separate `test(...)` commit** so the format commit stays purely mechanical (blame accuracy / D-16).
- **Files modified:** `tests/test_session_endpoint_owner_scope.py`, `tests/test_security_regressions.py`, `tests/test_cookbook_dependency_completion_regression.py`.
- **Verification:** Full suite back to the 20-failure baseline; format `--check` stable.
- **Committed in:** `cbb208d` (Task 3 test-fix commit).

---

**Total deviations:** 3 auto-fixed (2 blocking, 1 bug).
**Impact on plan:** All three were necessary to honor the behavior-preserving mandate; the locked D-18 sequence and the three-commit shape were preserved (format commit remained purely mechanical). No scope creep beyond config + the three minimal test-assertion fixes.

## Final per-file-ignores set

Top-level `ignore`: `E501, E402, B904, E712, E711, B008` (documented-idiom conflicts).

Per-file-ignores:
- `src/database.py` = F401, F403 (re-export shim, no `__all__`)
- `tests/**` = F401, F811 (side-effect imports, fixture reuse)
- ~27 individual files for localized residuals: B007/B023/B905/B006/B011/B017/B018/B026/E731/E741/F841/F403/W291 (see `pyproject.toml` for the exact map). Each would require a logic edit to "fix" — out of scope for a behavior-preserving pass.

## Ruff finding counts

- Baseline (config added, no fixes): **3548**
- After safe `--fix`: **644**
- After `ruff format` + Task-4 ignores: **0** (`ruff check .` exit 0)

## Issues Encountered

- **Local Python 3.14 env cannot gate the suite** — it lacks runtime deps (`markdown`, `pytest-asyncio`, etc.) producing 58 collection errors. Resolved by building `odyssey-test:01-02` from `python:3.12-slim` with `requirements.lock` + `requirements-dev.lock` (the same methodology Plan 01-01 used) and running pytest there. This is the real behavior contract.
- **Stale `__pycache__` poisoned import-order bisection** — when reverting `.py` files to bisect the circular-import regression, leftover `.pyc` from prior fixed-code runs (written into the bind-mounted worktree by the container) caused false negatives. Resolved by purging `__pycache__`/`.pyc` before every container pytest run. (`__pycache__` is gitignored; no tracked files touched. `git clean` was NOT used — prohibited.)

## Known Stubs

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- ruff lint + format gates are at a true zero baseline — ready for the CI quality-gate (01-05) to hard-block on `ruff check` / `ruff format --check`, and for the scorecard (01-04) to record the clean lint/format metric.
- The `python:3.12-slim` + locked-deps test image is the established behavior-contract runner for all later refactor phases (local non-container pytest is unreliable).
- Note for CI: enable blame-ignore locally with `git config blame.ignoreRevsFile .git-blame-ignore-revs` (GitHub honors it automatically).

## Self-Check: PASSED

- FOUND: `.git-blame-ignore-revs`
- FOUND: `.planning/phases/01-tooling-foundation-baseline-scorecard/01-02-SUMMARY.md`
- FOUND commits: d419f84, 81de772, 1e2074f, 1b268c7, cbb208d, 1555c8c

---
*Phase: 01-tooling-foundation-baseline-scorecard*
*Completed: 2026-06-03*
