# Project Research Summary

**Project:** Odysseus — Behavior-Preserving Engineering Modernization
**Domain:** Large Python/FastAPI brownfield monolith — quality gates, structural decomposition, security audit
**Researched:** 2026-06-03
**Confidence:** HIGH

## Executive Summary

Odysseus is a mature, feature-complete self-hosted AI assistant platform that has accumulated significant structural debt: four Python god-files totaling ~13,000 lines, four frontend JS files totaling ~26,000 lines, no CI pipeline, no dependency lockfile, no linter, and no type enforcement. The correct modernization approach for this class of codebase is a disciplined sequence of measurement → safety baseline → structural decomposition → deeper refactors → security audit, never skipping steps or parallelizing work that shares a dependency. All four research threads independently converged on this ~4–5 phase structure, which is a strong signal. The existing codebase already has good patterns (route factories, `initialize_managers()` DI, `agent_tools.py` facade precedent) — the work extends these patterns rather than replacing them.

The single most critical gate in this entire milestone is: **fill direct test coverage on each god-file before splitting it**. `src/tool_implementations.py` (~4100 lines) and `routes/email_routes.py` (~3200 lines) have no dedicated test files. Refactoring them without direct coverage is blind surgery — the existing indirect tests exercise happy paths but leave error branches, auth checks, and edge cases dark. Every phase ordering decision flows from this gate. The test suite is the behavior contract; the coverage map tells you which parts of that contract are actually enforced.

The security picture is better than a cold-start audit: Odysseus has a `THREAT_MODEL.md`, `SECURITY.md`, per-owner row scoping, and a `test_security_regressions.py` suite. The audit extends this foundation rather than replacing it. The highest-risk open items are the SQLite concurrency gap (no WAL/busy_timeout confirmed by CONCERNS.md — but see the unresolved conflict flag below), 21 raw `sqlite3.connect()` sites that bypass any engine-level pragma config, broad `except Exception` handlers that silently swallow `OperationalError` (confirmed data-loss risk at `task_routes.py:476`), and the encryption key co-located with the database file. These are concrete, fixable, and high-value.

## Key Findings

### Recommended Stack

See `.planning/research/STACK.md` for full detail. This is not a greenfield stack decision — it is an additive tooling layer on top of the existing Python 3.12 / FastAPI / SQLAlchemy / SQLite / vanilla-JS stack. No framework migrations are permitted.

The additive tooling layer, in adoption priority order:

**Core technologies:**
- **ruff 0.15.15**: Lint + format (Python) — replaces black + isort + flake8 in one Rust binary; handles `datetime.utcnow()` bulk-flagging via `DTZ`/`UP` rule sets; must be the first CI gate added
- **mypy 2.1.0**: Type checking — adopt with inverted-strictness model (lenient global baseline + per-module overrides tightened as god-files are split); do not run `--strict` globally on day one
- **coverage.py 7.14.1 + pytest-cov 7.1.0**: Coverage measurement — goal is to produce the coverage *map* before refactoring, not to gate on a percentage; do not add `fail_under` until after the baseline is measured
- **uv 0.11.18** (or pip-tools 7.5.3): Dependency pinning — compile `requirements.in` → `requirements.txt` lockfile; `requirements.txt` is currently fully unpinned, a security and reproducibility risk
- **bandit 1.9.4 + semgrep 1.164.0**: SAST — complementary, not redundant; bandit for fast code-smell scan, semgrep for systematic OWASP Top 10 with FastAPI-specific rules
- **pip-audit 2.10.0**: Dependency CVE scanning — requires pinned deps to produce meaningful output; gate only after lockfile is established
- **ESLint 10.4.1 + Prettier 3.8.3**: JS tooling — lowest priority item; establish Python CI first

CI pipeline order (fastest/cheapest first): ruff format → ruff check → mypy → pytest (+cov) → bandit → pip-audit.

### Expected Workstreams

See `.planning/research/FEATURES.md` for full detail. This milestone has no new product features — "features" are engineering workstreams.

**Must have (table stakes for a credible modernization):**
- Baseline scorecard (module sizes, type %, lint count, coverage map, security findings, perf benchmark) — must be first; all other workstreams set targets against it
- Ruff lint + format enforcement in CI
- `datetime.utcnow()` replacement across 24 files (Python 3.14 removal; documented regression history)
- Dependency pinning + lockfile
- CI quality gates (all tools blocking PRs)
- mypy gradual adoption (enforce in CI before god-file splits)
- Test coverage baseline + gap-filling on the two coverageless god-files — must precede their splits
- God-file split (Python): `tool_implementations.py`, `email_routes.py`, `cookbook_routes.py`, `model_routes.py`, `gallery_routes.py`, `agent_loop.py`, `task_scheduler.py`, `builtin_actions.py`
- Security audit (systematic OWASP ASVS L1 pass)

**Should have (high-value differentiators):**
- SQLite WAL + `busy_timeout` hardening (correctness fix, not perf optimization; confirmed data-loss risk)
- Global singleton → explicit DI reduction (eliminate remaining `set_*` wiring)
- Broad `except Exception` narrowing (1104 handlers; security-relevant sites identified by audit)
- God-file split (Frontend JS): `document.js` (9731 lines), `slashCommands.js`, `emailLibrary.js`, `notes.js`
- Orphaned-code cleanup from two recent reverts (`67b63e9`, `1f6c5ac` still on main)

**Defer (P3 / capacity-dependent):**
- Migration system hardening (ledger table or Alembic brownfield bootstrap) — after `core/database.py` split
- Structured logging (stdlib → structlog/loguru) — after god-file splits expose natural per-module logger boundaries

**Explicitly out of scope:** framework migration, schema redesign, API/SSE contract changes, frontend framework adoption, speculative performance re-architecture, new product features, big-bang rewrites.

### Architecture Approach

See `.planning/research/ARCHITECTURE.md` for full detail. The target architecture is an extension of patterns already present. The codebase is a modular monolith: `app.py` (wiring only) → `routes/<domain>/` (thin HTTP factories) → `src/*_handler.py` + `services/*/` (stateful orchestration + capabilities) → `core/` (shared infra). The decomposition work converts god-files into packages using the facade re-export pattern already established by `src/agent_tools.py`.

**Major decomposition targets:**
1. `src/tool_implementations.py` → `src/tool_implementations/` package with `__init__.py` re-export facade and one submodule per tool family
2. `routes/email_routes.py` → `routes/email/` sub-router package with `account_helpers`, `message_helpers`, `send_helpers`, `sync_helpers`, `folder_helpers`
3. `routes/cookbook_routes.py`, `routes/model_routes.py`, `routes/gallery_routes.py` → same sub-router pattern
4. `src/agent_loop.py` → `src/agent_loop/` package after tool system is stable
5. DI migration: eliminate `set_*` setters in priority order (low-risk: `set_memory_manager`, `set_webhook_manager` first; high-risk: `set_session_manager` last)

The safe extraction protocol is: **cover → extract → verify → repeat**. Each extraction is a single PR, moves code verbatim without changing call signatures, maintains the re-export facade throughout the transition, and the test suite must be green before and after every commit.

**Critical layer rules:** `routes/` must not cross-import other `routes/`. `core/` has no upward dependencies. `services/` must not import from `src/` or `routes/`. These are existing conventions — enforce, do not introduce competing ones.

### Critical Pitfalls

See `.planning/research/PITFALLS.md` for all 13 pitfalls with phase-to-phase mapping. Top 5:

1. **Refactoring before direct coverage exists** — `tool_implementations.py` and `email_routes.py` have no dedicated test files. A green suite after splitting is not evidence of behavior preservation if the tests never exercised the moved branches directly. Hard gate: no split PR merges until a per-file direct coverage threshold is established. (Phase 1 gate)

2. **Auth/owner-scoping regression during route splits** — In a god-file, `owner` is in closure scope for every handler. When helpers are extracted, `owner` must become an explicit function parameter. Missing one extraction point compiles cleanly, passes all existing tests, and silently enables cross-user data access. Mandatory: every route-extraction PR includes at least one cross-owner access test for each extracted data-fetching function. (Phase 2 gate)

3. **SQLite `OperationalError` silently swallowed** — No WAL, no `busy_timeout` (per CONCERNS.md — see unresolved conflict flag), 21 raw `sqlite3.connect()` sites bypassing the SQLAlchemy engine pragma config, and confirmed `except sqlite3.OperationalError: count = 0` at `task_routes.py:476`. Fix requires WAL pragma in both the engine event listener AND a `raw_sqlite_connect()` helper covering all 21 raw sites. (Phase 2/3 action)

4. **Scope creep from refactor into redesign** — A split PR that introduces new Pydantic models, changed call signatures, or new `services/` directories is a redesign, not a refactor. PR template enforcement: "Does this PR change any calling convention, return type, or observable behavior? If yes, split into a separate PR." (Phase 3 enforcement)

5. **`datetime.utcnow()` replacement creating new naive/aware mixing bugs** — The calendar/scheduler cluster already has multiple documented UTC regressions. Bulk replacement breaks downstream comparisons and ICS format strings. Each call site requires tracing to all downstream consumers; fix producer + consumers in the same commit. (Phase 3 sequencing)

## Implications for Roadmap

All four research threads converged on this phase structure independently. This convergence is strong evidence the ordering is correct.

### Phase 1: Tooling Foundation + Baseline Scorecard
**Rationale:** You cannot define "done" without a before-state measurement. The CI enforcement harness must exist before any other work can be verified. All subsequent phases depend on the scorecard metrics.
**Delivers:** pyproject.toml with ruff + mypy + coverage config; CI pipeline; `requirements.in` lockfile via `uv pip compile`; baseline scorecard (module sizes, lint count, type %, line coverage map, security findings list, perf benchmark); async test stability confirmed under `--cov`; orphaned-code cleanup from reverted PRs
**Addresses:** Baseline scorecard, ruff enforcement, dependency pinning + lockfile, CI quality gates, mypy Phase 1 baseline
**Avoids:** Pitfalls 8 (mypy over-aggressive), 9 (ruff rule churn), 10 (transitive dep pinning breakage), 11 (flaky async tests under coverage)
**Research flag:** Standard patterns — skip research-phase. All tooling versions and configurations are decided in STACK.md.

### Phase 2: Safety Baseline — Coverage Gap-Fill + SQLite Hardening
**Rationale:** The coverage gate must be established before any god-file is touched. This is the most likely phase to be skipped under schedule pressure and the most dangerous to skip. SQLite WAL hardening is a correctness fix with confirmed data-loss risk — it lands here, not in the security audit phase, because it is a single config change that can be made safely as soon as CI is green.
**Delivers:** Direct test files for `tool_implementations.py` and `email_routes.py` covering critical branches; per-file coverage thresholds set in scorecard; `PRAGMA journal_mode=WAL` + `busy_timeout` in engine event listener; `raw_sqlite_connect()` helper covering all 21 raw connection sites; authenticated endpoint enumeration list for security audit checklist; cross-owner test baseline
**Addresses:** Test coverage baseline + gap-filling, SQLite WAL + busy_timeout hardening
**Avoids:** Pitfalls 1 (refactoring without coverage), 5 (SQLite OperationalError swallowed), 7 (auth regression — establish cross-owner test pattern now), 12 (false security confidence)
**Research flag:** Code-read required before planning — resolve the WAL conflict (see Gaps section). Standard patterns otherwise.

### Phase 3: Structural Decomposition — Python God-Files
**Rationale:** With CI green and direct coverage in place, the god-file splits are safe. Execute in documented order: `tool_implementations.py` first (largest), then `email_routes.py` (highest auth-regression risk), then cookbook/model/gallery (helpers already exist), then `agent_loop.py` (after tool system is stable). Never split `tool_implementations.py` and `agent_loop.py` in the same sprint — they are tightly coupled through `tool_execution.py`.
**Delivers:** All Python god-files converted to focused packages with re-export facades; `tool_implementations/` package (8 submodules by tool family); `routes/email/`, `routes/cookbook/`, `routes/model/`, `routes/gallery/` sub-router packages; `src/agent_loop/` package; every split PR accompanied by cross-owner access tests; mypy per-module overrides tightened on newly created submodules; `datetime.utcnow()` replaced per-file with downstream tracing; low-risk DI setters eliminated (`set_memory_manager`, `set_webhook_manager`)
**Addresses:** God-file split (Python), type-hint adoption (mypy Phase 2), `datetime.utcnow()` replacement, `except Exception` narrowing, DI migration Phase B (low-risk setters)
**Avoids:** Pitfalls 2 (stub-mismatch after split), 3 (import-order singleton breakage), 4 (scope creep into redesign), 6 (utcnow naive/aware mixing), 13 (secret handling regression)
**Research flag:** Pre-planning code-read: verify `tool_implementations.py` / `agent_loop.py` coupling through `tool_execution.py` before scheduling. Standard patterns otherwise.

### Phase 4: Structural Decomposition — Frontend + DI Hardening
**Rationale:** Frontend splits are independent of Python splits. High-risk `set_session_manager` migration must wait until all route splits are stable (it touches agent/streaming path). Migration system hardening requires `core/database.py` to be split first.
**Delivers:** `document.js`, `slashCommands.js`, `emailLibrary.js`, `notes.js` reorganized into focused ES modules; `set_session_manager` eliminated; `initialize_managers()` + factory closure is the sole wiring mechanism; migration system hardening (schema_migrations ledger table or Alembic `alembic stamp head` — decide at planning based on code-read); structured logging foundations where natural per-module boundaries emerged
**Addresses:** God-file split (frontend JS), DI migration Phase B (high-risk setters), migration system hardening, structured logging
**Avoids:** Anti-pattern of pushing application-lifetime singletons into `Depends()` chains
**Research flag:** Migration system hardening needs a code-read decision at planning time: read the existing 37 `_migrate_*` functions to determine whether an idempotency check already exists before choosing ledger-table vs. Alembic approach.

### Phase 5: Security Audit
**Rationale:** Audit comes last because (a) code surfaces are cleaner after splits, (b) the lockfile enables CVE scanning, (c) the endpoint enumeration from Phase 2 provides the audit checklist, (d) the security regression suite has been extended through Phase 3 split PRs.
**Delivers:** OWASP ASVS L1 checklist completed across all 9 relevant categories; semgrep `p/python` + `p/fastapi` + `p/secrets` scan triaged; `pip-audit` CVE report resolved; `_safe_ident()` allow-list helper implemented for f-string SQL sites in `core/database.py`; encryption key path hardened with env-var override + startup health check; reverted PR code (`1f6c5ac`) verified clean; security regression suite complete
**Addresses:** Systematic security audit, remaining `except Exception` narrowing at security-relevant DB paths, secret storage hardening
**Avoids:** Pitfall 12 (false confidence — enumeration checklist ensures 100% of authenticated endpoints have explicit cross-owner tests)
**Research flag:** Standard patterns (ASVS L1 is a published checklist; semgrep FastAPI rules are maintained). No research-phase needed.

### Phase Ordering Rationale

- **Scorecard before everything:** Without the before-state measurement, there is no definition of done.
- **Coverage before splits — non-negotiable:** The most likely constraint to be violated under schedule pressure. A green suite on refactored code without direct coverage is not evidence of behavior preservation.
- **SQLite WAL in Phase 2, not Phase 5:** This is a correctness fix with confirmed data-loss potential. Deferring increases the window during which refactored code raising write frequency can trigger silent failures.
- **`datetime.utcnow()` interleaved with splits (Phase 3), not bulk:** Each replacement requires tracing downstream consumers. Per-file replacement during splits keeps the scope bounded and targeted.
- **DI migration split across Phase 3 (low-risk) and Phase 4 (high-risk):** `set_session_manager` touches the streaming path — migrate last, after all route splits are stable.
- **Frontend splits in Phase 4:** No ordering dependency on Python splits. Keeping Phase 3 focused on Python god-files reduces concurrency risk.

### Research Flags

**Needs targeted research or code-read during planning:**
- **UNRESOLVED CONFLICT — SQLite WAL (code-read required before Phase 2 planning):** CONCERNS.md states WAL/`busy_timeout` are absent. ARCHITECTURE.md states "WAL/pragmas configured via engine event listeners." These are in direct conflict. Before Phase 2 planning, read `core/database.py` lines ~40–60 (the `set_sqlite_pragma` event listener) to determine ground truth. Do not treat either description as settled fact. If WAL is already present, Phase 2 scope reduces to the 21 raw `sqlite3.connect()` sites only.
- **Phase 3 pre-planning:** Verify `tool_implementations.py` / `agent_loop.py` coupling through `tool_execution.py` before sprint scheduling. These two must not be split in the same sprint.
- **Phase 4 (migration hardening):** Read `core/database.py`'s `_migrate_*` structure before committing to ledger-table vs. Alembic. The decision significantly affects scope.

**Standard patterns (skip research-phase during planning):**
- **Phase 1:** All tooling configurations are verified and documented in STACK.md with exact versions.
- **Phase 2 (SQLite fix, post-code-read):** Single-line pragma change; mechanics are well-understood.
- **Phase 5:** ASVS L1 is a published checklist; semgrep FastAPI rules are maintained by Semgrep.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All versions verified against PyPI at research time; behavior verified against official docs via Context7. No speculative choices. |
| Features/Workstreams | HIGH | Grounded in first-party codebase analysis (CONCERNS.md, ARCHITECTURE.md) + current Python ecosystem practice. Workstream ordering driven by confirmed dependency constraints. |
| Architecture | HIGH | Patterns verified against FastAPI official docs + existing codebase conventions. Target structure extends existing patterns — low novelty risk. |
| Pitfalls | HIGH | Grounded in first-party codebase evidence (CONCERNS.md confirms specific line numbers, missing test files, raw connection count). SQLite concurrency behavior verified against SQLite docs and multiple sources. |

**Overall confidence:** HIGH

### Gaps to Address

- **SQLite WAL state — unresolved conflict (code-read required):** CONCERNS.md and ARCHITECTURE.md conflict on whether WAL is currently configured. Must be resolved by reading `core/database.py` before Phase 2 planning. This is the only factual conflict across all four research files.

- **Coverage baseline values unknown until measured:** The scorecard will reveal actual per-file coverage percentages. The assumption that `tool_implementations.py` and `email_routes.py` have thin direct coverage is confirmed by the absence of dedicated test files, but exact indirect coverage is unknown. Phase 2 scope depends on these numbers.

- **Migration system complexity:** Whether the 37 `_migrate_*` functions are idempotent, whether any are already short-circuited, and whether a ledger table exists is unconfirmed. Phase 4 planning code-read will resolve this.

- **Reverted PR orphaned code scope:** Two recent reverts (`67b63e9`, `1f6c5ac`) are on main. A `git diff` against each during Phase 1 scorecard work will bound the cleanup scope.

## Sources

### Primary (HIGH confidence)
- `.planning/codebase/CONCERNS.md` — first-party codebase audit; specific confirmed issues (21 raw sqlite3.connect sites, missing test files, OperationalError swallowing line numbers, utcnow file count)
- `.planning/codebase/ARCHITECTURE.md` — confirmed module structure, singleton wiring, lazy-import convention, facade precedent
- `.planning/codebase/TESTING.md` — confirmed stub-by-path pattern, asyncio_mode=auto, no coverage gate
- `.planning/PROJECT.md` — behavior-preserving constraint, coverage-before-refactor key decision, out-of-scope boundaries
- Context7 `/astral-sh/ruff` — ruff configuration, rule sets, pyproject.toml format
- Context7 `/python/mypy` — gradual typing strategy, per-module overrides
- Context7 `/astral-sh/uv` — uv pip compile, requirements.txt export
- Context7 `/pytest-dev/pytest-cov` — configuration, pyproject.toml integration
- Context7 `/pycqa/bandit` — config, baseline workflow
- Context7 `/pypa/pip-audit` — CLI options, requirements.txt scanning
- FastAPI official docs (Context7 `/fastapi/fastapi`) — router nesting, include_router, Depends scope
- [FastAPI: Bigger Applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/) — sub-router patterns
- [datetime.utcnow deprecation — miguelgrinberg.com](https://blog.miguelgrinberg.com/post/it-s-time-for-a-change-datetime-utcnow-is-now-deprecated)
- [SQLite concurrent writes](https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/) — WAL mode, BEGIN IMMEDIATE, busy_timeout behavior

### Secondary (MEDIUM confidence)
- [FastAPI best practices — zhanymkanov](https://github.com/zhanymkanov/fastapi-best-practices) — module structure, DI, service extraction heuristics
- [FastAPI DI discussion #8968](https://github.com/fastapi/fastapi/discussions/8968) — app.state migration stepping-stone
- [mypy incremental adoption — eightfold.ai](https://eightfold.ai/engineering-blog/static-type-checking-large-scale-python-codebase/) — per-module override strategy case study
- [SQLite BEGIN IMMEDIATE busy_timeout gotcha](https://berthug.eu/articles/posts/a-brief-post-on-sqlite3-database-locked-despite-timeout/)
- [pytest-asyncio changelog](https://pytest-asyncio.readthedocs.io/en/stable/reference/changelog.html) — class-scoped loop fixture bugs

### Tertiary (LOW confidence)
- [Semgrep vs Bandit 2026 — DEV Community](https://dev.to/rahulxsingh/semgrep-vs-bandit-python-security-scanning-compared-2026-5e5j) — complementary tool roles (single source)

---
*Research completed: 2026-06-03*
*Ready for roadmap: yes*
