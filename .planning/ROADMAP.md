# Roadmap: Odysseus Engineering Modernization

## Overview

A five-phase behavior-preserving modernization of the Odysseus codebase, sequenced by dependency: establish the measurement baseline and enforcement harness first, fill direct test coverage before touching any god-file, decompose the Python god-files with per-extraction CI verification, harden the frontend and data layer while completing dependency-injection cleanup, and close with a systematic OWASP ASVS L1 security audit against clean, split code. The existing 355-file pytest suite is the behavior contract throughout; the application's HTTP/SSE APIs, schema, and UX are unchanged at every phase boundary.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Tooling Foundation & Baseline Scorecard** - Establish the measurement baseline, CI enforcement harness, and dependency lockfile before any refactoring begins
- [ ] **Phase 2: Safety Baseline — Coverage & SQLite Hardening** - Fill direct test coverage on the two coverageless god-files and resolve the SQLite WAL/busy_timeout correctness risk
- [ ] **Phase 3: Backend God-File Decomposition** - Split all Python god-files into focused packages using the existing facade pattern; add type hints and replace deprecated datetime APIs as files are touched
- [ ] **Phase 4: Frontend Splits, DI Hardening & Data Layer** - Reorganize JS god-files into ES modules, eliminate remaining setter-wiring, harden the migration mechanism, and narrow swallowed OperationalErrors
- [ ] **Phase 5: Security Audit** - Systematic OWASP ASVS L1 pass across auth, per-owner scoping, input handling, secret storage, and dependencies against the now-clean, fully-covered codebase

## Phase Details

### Phase 1: Tooling Foundation & Baseline Scorecard

**Goal**: CI gates every PR on ruff, mypy, pytest, bandit, and pip-audit; an objective baseline scorecard captures the before-state for all tracked metrics; dependencies are pinned in a verified lockfile; the two reverted PRs' orphaned code is audited and removed
**Depends on**: Nothing (first phase)
**Requirements**: BASE-01, BASE-02, TOOL-01, TOOL-02, TOOL-03, TOOL-04
**Success Criteria** (what must be TRUE):

  1. CI pipeline blocks merges on ruff lint, ruff format, mypy (zero-error lenient baseline), pytest, bandit, and pip-audit — verified by a deliberate violation failing the PR check
  2. Baseline scorecard exists in `.planning/` with measured values for: per-module line counts, mypy-typed %, ruff finding count, security findings list, per-file test-coverage map, startup/key-path perf benchmark, and authenticated endpoint enumeration list; target thresholds are recorded alongside
  3. A lockfile (`requirements.txt` / `requirements.lock`) generated via `uv pip compile` or `pip-compile` on a clean Docker image is committed; `pip install -r requirements.lock` on a second clean Linux environment succeeds without `ResolutionImpossible`
  4. `git grep` for dead code from reverts `67b63e9` and `1f6c5ac` returns no dangling imports, unreachable config, or stub code; `ruff check` finds no `F401` or `F811` for the removed symbols
  5. `mypy` runs in CI with zero errors using an inverted-strictness config (lenient global baseline, per-module overrides via `[[tool.mypy.overrides]]`); ruff version is pinned to an explicit version with a stable, explicit rule set — no `select = ["ALL"]` and no `preview = true`

**Plans**: 5 plansPlans:
**Wave 1**

- [x] 01-01-PLAN.md — Dependency lockfiles (core/optional/dev .in + Docker-compiled hashed locks) + SC#3 second-env validation + coverage-stability prereq (TOOL-03)

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-02-PLAN.md — ruff adoption to a zero-finding baseline via the D-18 atomic config→fix→format sequence + .git-blame-ignore-revs (TOOL-01)

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md — mypy lenient inverted-strictness config to zero errors + coverage/bandit config skeletons (TOOL-02)

**Wave 4** *(blocked on Wave 3 completion)*

- [ ] 01-04-PLAN.md — scripts/scorecard.py (7-metric JSON-first generator with --write/--check ratchet) + baseline.json + SCORECARD.md (BASE-01)

**Wave 5** *(blocked on Wave 4 completion)*

- [ ] 01-05-PLAN.md — parallel quality-gate CI workflow + bandit suppression audit + BASE-02 revert audit + SC#1 deliberate-violation demonstration (TOOL-04, BASE-02)

**Phase note (pre-planning code-read):** Before planning this phase, verify the lockfile generation environment matches CI (Docker `python:3.12-slim`). The chromadb/fastembed/onnxruntime group is the highest risk for transitive dep conflicts (Pitfall 10) — consider a separate `requirements-ml.txt` group.

### Phase 2: Safety Baseline — Coverage & SQLite Hardening

**Goal**: Direct characterization tests exist for `src/tool_implementations.py` and `routes/email_routes.py`; per-file coverage thresholds are enforced in CI; the SQLite WAL/busy_timeout correctness issue is resolved and all 21 raw `sqlite3.connect()` sites are routed through a single hardened helper; cross-owner isolation tests cover the auth boundary for all authenticated data-fetching endpoints enumerated in Phase 1
**Depends on**: Phase 1
**Requirements**: COV-01, COV-02, COV-03, DATA-01
**Success Criteria** (what must be TRUE):

  1. Dedicated test files `tests/test_tool_implementations*.py` and `tests/test_email_routes*.py` exist; `pytest --cov=src/tool_implementations --cov=routes/email_routes --cov-report=term-missing` shows all critical branches (error handling, auth checks, tool dispatch arms) covered directly — not only via indirect route-stack invocation
  2. A per-file coverage threshold is defined in `pyproject.toml` (`[tool.coverage.report] fail_under = N`); a test deletion deliberately causes the CI coverage check to fail, proving the gate is enforced
  3. `grep -r 'sqlite3.connect(' src/ routes/ core/ mcp_servers/` returns exactly one result (the `raw_sqlite_connect()` helper definition); `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout` appear in both the SQLAlchemy engine event listener in `core/database.py` and inside `raw_sqlite_connect()`; DB-contention tests exist and pass
  4. Cross-owner isolation tests exist for every authenticated data-fetching endpoint in the enumeration list from Phase 1; each test asserts that a second authenticated user receives 403 or an empty result when requesting the first user's data — no new tests use closure-scope `owner` access

**Phase note (mandatory code-read before planning):** CONCERNS.md and ARCHITECTURE.md conflict on whether WAL/`busy_timeout` are currently configured in `core/database.py`. Before planning DATA-01, read `core/database.py` lines ~40–60 (the `set_sqlite_pragma` event listener) to determine ground truth. If WAL is already present, DATA-01 scope reduces to the 21 raw `sqlite3.connect()` sites and the `BEGIN IMMEDIATE` write-transaction fix only.

### Phase 3: Backend God-File Decomposition

**Goal**: All in-scope Python god-files are decomposed into focused packages with re-export facades following the existing `agent_tools.py` precedent; `src/tool_implementations.py` is split first (before `agent_loop.py`); `datetime.utcnow()` is replaced per-file with downstream consumer tracing; modern type hints are added to each newly created submodule; low-risk DI setters are eliminated
**Depends on**: Phase 2
**Requirements**: REFAC-01, REFAC-02, REFAC-03, REFAC-04, TYPE-01, TOOL-05
**Success Criteria** (what must be TRUE):

  1. `from src.tool_implementations import do_web_search` (and all other `do_*` public symbols) continues to resolve without error after the split — the `__init__.py` re-export facade is verified by `python -c "from src.tool_implementations import <all_public_names>"` passing clean
  2. All five route god-files (`email_routes.py`, `cookbook_routes.py`, `model_routes.py`, `gallery_routes.py`) and `src/agent_loop.py`, `src/task_scheduler.py`, `src/builtin_actions.py` are converted to packages or have helper extractions; HTTP prefixes, SSE event names, and response shapes are identical before and after — verified by the full pytest suite being green after each extraction commit
  3. `grep -rn 'utcnow' .` returns zero results; no new `# type: ignore` without a specific error code appears in any file touched by this phase; no `.replace(tzinfo=None)` appeared in code that was not already there before the replacements — verified by regression tests modeled on the existing `test_calendar_rrule_until_utc.py` pattern for the calendar/scheduler cluster
  4. All newly created submodules have `[[tool.mypy.overrides]]` entries with stricter type-checking flags than the global baseline; the mypy-typed % metric in the scorecard improves from its Phase 1 baseline
  5. `set_memory_manager` and `set_webhook_manager` setter calls are removed from `app.py`; the corresponding globals are deleted; `initialize_managers()` and route factory `deps` carry these values instead — verified by `grep -r 'set_memory_manager\|set_webhook_manager' src/ routes/ app.py` returning zero results

**Phase note (mandatory pre-planning code-read):** Verify `tool_implementations.py` / `agent_loop.py` coupling through `tool_execution.py` before scheduling. These two must not be split in the same sprint. `tool_implementations.py` must be split first because `agent_loop.py` depends on `tool_execution.py` which imports from `tool_implementations`. Concurrent edits to both produce entangled diffs. Read the import graph in `src/agent_tools.py` and `src/tool_execution.py` before splitting `agent_loop`.

### Phase 4: Frontend Splits, DI Hardening & Data Layer

**Goal**: The four frontend JS god-files are reorganized into focused ES modules with no build step; `set_session_manager` (the last and highest-risk setter) is eliminated; the migration mechanism is hardened so migrations do not re-scan every table on boot; swallowed `sqlite3.OperationalError` paths are narrowed and logged
**Depends on**: Phase 3
**Requirements**: FE-01, DI-01, DATA-02, DATA-03
**Success Criteria** (what must be TRUE):

  1. `document.js` (~9700 lines), `slashCommands.js` (~6100), `emailLibrary.js` (~5200), and `notes.js` (~5000) are reorganized into focused ES modules; `<script type="module">` loads succeed in the browser with no JS console errors; SPA behavior and appearance are identical to pre-split (manual smoke test of core agent, email, and notes workflows)
  2. `grep -r 'set_session_manager' src/ routes/ app.py` returns zero results; `initialize_managers()` + route factory closure is the sole wiring mechanism for all long-lived singletons; no application-lifetime singleton was pushed into a `Depends()` chain
  3. The migration mechanism short-circuits already-applied migrations: either a `schema_migrations` ledger table records applied migration names, or Alembic `stamp head` bootstraps the existing schema so only new migrations run; startup migration time drops materially versus the Phase 1 benchmark; all existing databases still load unchanged — verified by loading a pre-migration DB and confirming schema shape is unchanged
  4. `grep -r 'OperationalError' routes/ src/ core/' returns no bare `except ... pass` or silent `count = 0` assignments; every caught `OperationalError` either has a `logger.warning` with query context or a `# intentional-fail-soft:` comment explaining why silent degradation is correct

**Phase note (mandatory pre-planning code-read):** Before committing to ledger-table vs. Alembic for DATA-02, read `core/database.py`'s `_migrate_*` structure. Determine: (a) whether any idempotency check already exists, (b) whether any migration is already short-circuited, (c) whether a ledger table or `alembic_version` table is already present. The decision significantly affects scope. Full Alembic adoption (autogenerate from 24 tables, batch migration setup) is v2 scope (DATA-04) unless the code-read shows it is low-cost from this starting point.

### Phase 5: Security Audit

**Goal**: A systematic OWASP ASVS L1 audit is complete across all 9 relevant categories; all high-severity findings from bandit, semgrep, and pip-audit are closed; f-string SQL injection sites are parameterized or allow-listed; secret-storage key co-location is hardened; every authenticated endpoint has a verified cross-owner isolation test
**Depends on**: Phase 4
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-04, SEC-05
**Success Criteria** (what must be TRUE):

  1. A triaged ASVS L1 findings list exists in `.planning/` covering all 9 applicable ASVS L1 categories; the authenticated endpoint enumeration list from Phase 1 shows 100% of data-fetching endpoints have a cross-owner isolation test — verified by the count matching the enumeration total
  2. `pip-audit` run against the lockfile produces zero high-severity or critical CVE findings (or each remaining finding has a documented triage entry explaining why it is not applicable); `bandit` and `semgrep p/fastapi` produce no high-severity unaddressed findings — a curated `.bandit` / `.semgrep` ignore config documents all suppressions
  3. `grep -rn 'f".*{' core/database.py routes/'` finds no f-string SQL construction carrying user-controlled input; the `_safe_ident()` allow-list helper (or equivalent parameterization) is in place for all schema-derived identifier interpolation sites
  4. `src/secret_storage.py`'s key path has an environment-variable override (`APP_KEY_PATH` or equivalent); a startup health check logs `ERROR` (not silent degradation) if the key file is inaccessible; a round-trip `encrypt(decrypt(value)) == value` test exists that exercises the real filesystem path (not mocked) against a temp directory
  5. The full pytest suite is green with no skipped security tests; `tests/test_security_regressions.py` has grown with at least one new test per split route module from Phases 3–4; the mypy-typed % and ruff finding count in the scorecard have each improved from their Phase 1 baseline values

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Tooling Foundation & Baseline Scorecard | 0/5 | Not started | - |
| 2. Safety Baseline — Coverage & SQLite Hardening | 0/TBD | Not started | - |
| 3. Backend God-File Decomposition | 0/TBD | Not started | - |
| 4. Frontend Splits, DI Hardening & Data Layer | 0/TBD | Not started | - |
| 5. Security Audit | 0/TBD | Not started | - |
