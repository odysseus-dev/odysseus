# Feature Research — Modernization Workstreams

**Domain:** Behavior-preserving modernization + security audit of a large Python/FastAPI monolith
**Researched:** 2026-06-03
**Confidence:** HIGH (grounded in codebase analysis + current Python ecosystem practice)

> NOTE: "Features" here means *workstreams* — the categories of engineering work this modernization
> milestone covers. There are no new product features; the application behaves identically after
> every workstream completes.

---

## Table Stakes (Must Do — Credible Modernization Requires These)

These workstreams must be completed for the milestone to be credible. Skipping any means the
codebase ends the milestone with a known, documented gap that will re-accumulate debt.

| Workstream | Why Required | Complexity | Ordering Notes |
|---|---|---|---|
| **Baseline scorecard** | You cannot define "done" without an objective measurement of where you start — module sizes, type coverage %, lint error count, security findings, test coverage map, performance benchmark | LOW | **Must be first.** All other workstreams set targets against it. |
| **Lint/format enforcement (ruff)** | Unpinned formatter = code style diverges across contributors. Ruff replaces black + isort + flake8 in one pass. Must be enforced in CI, not just available locally. | LOW | After scorecard. Unblocks everything else by establishing a clean diff baseline. |
| **Deprecated API replacement (`datetime.utcnow`)** | 24 files affected. Python 3.12 deprecated it; Python 3.14 removes it. Already caused documented UTC regression bugs in calendar/scheduler. Cheap to fix mechanically; dangerous to defer. | LOW | After ruff is enforced (ruff rule `DTZ` flags all occurrences). Independent of god-file split. |
| **Dependency pinning + lockfile** | `requirements.txt` has zero pins. A single upstream bump (pydantic, SQLAlchemy, chromadb-client) can silently break behavior or introduce a CVE. CI must install from a lockfile. | LOW–MEDIUM | After scorecard; does not depend on any other workstream. `pip-compile` (pip-tools) is the minimal path; `uv lock` is the modern alternative. |
| **CI quality gates** | Ruff lint, ruff format check, mypy, pytest (coverage threshold), bandit/safety — all must gate PRs. Without CI enforcement every other workstream degrades on the next PR. | LOW–MEDIUM | After ruff, mypy stub, and lockfile workstreams are established. CI is the enforcement harness for all tooling decisions. |
| **Type-hint adoption (mypy, incremental)** | `list[X]` / `X \| None` modern syntax throughout. mypy enforced in CI. FastAPI is built around type hints; missing hints remove the compile-time safety net Pydantic provides. Apply mypy per-module with `# type: ignore` escapes allowed in first pass to avoid a big-bang gate. | MEDIUM | After ruff (ruff catches some annotation issues). Before god-file splits (splitting untyped code is harder to verify). |
| **Test coverage baseline + gap-filling** | `src/tool_implementations.py` (4144 lines) and `routes/email_routes.py` (3214 lines) have no dedicated tests. Coverage must be added **before** those files are split. Refactoring untested god-files is unsafe — regressions are invisible. Establish a per-file coverage threshold that must be met before any file is split. | MEDIUM–HIGH | **Must precede god-file splits.** Can proceed in parallel with type-hint adoption. |
| **God-file split (Python)** | `tool_implementations.py`, `email_routes.py`, `cookbook_routes.py`, `model_routes.py`, `gallery_routes.py`, `agent_loop.py`, `task_scheduler.py`, `builtin_actions.py`. Split follows the existing `*_helpers.py` / `setup_*_routes` convention; no new patterns introduced. | HIGH | After coverage baseline. Each file gets dedicated tests first, then is split incrementally. |
| **God-file split (Frontend JS)** | `document.js` (9731 lines), `slashCommands.js` (6099), `emailLibrary.js` (5217), `notes.js` (5009). Reorganize into focused ES modules; no framework adoption. Behavior preserved (SPA behavior + appearance unchanged). | MEDIUM–HIGH | Independent of Python splits. After scorecard. Frontend scope is structural reorganization only. |
| **Security audit (systematic)** | Builds on `THREAT_MODEL.md` + `SECURITY.md` + `tests/test_security_regressions.py`. Covers: dynamic SQL f-string interpolation sites (`core/database.py`, `routes/`), encryption key co-location with data, dependency CVE scan (pip-audit/safety), secret-storage docs/hardening, broad `except Exception` swallowing masking security errors. | MEDIUM–HIGH | After lockfile (CVE scan needs pinned deps). Security findings that require code changes to god-files should wait until after coverage baseline. |

---

## Differentiators (High-Value, Larger Effort)

These workstreams are material improvements that go beyond baseline hygiene. Each is valuable and
aligned with the milestone's goals but requires more planning, more risk management, or
carries higher consequence if done in the wrong order.

| Workstream | Value Proposition | Complexity | Ordering Notes |
|---|---|---|---|
| **SQLite WAL + busy_timeout hardening** | `check_same_thread=False` is set but `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout` are absent. Under async load (email pollers + scheduler + interactive requests hitting the same file), the app risks silent `OperationalError` → data loss at the `except` swallow sites. WAL + 5–30 s busy_timeout + routing the 21 ad-hoc `sqlite3.connect()` calls through a single shared helper resolves this. Measurably reduces a documented fragile area. | MEDIUM | After CI gates are green (change is testable). Add DB contention tests alongside the fix. |
| **Global singleton → explicit DI reduction** | The `set_session_manager` / `set_task_scheduler` / `set_memory_manager` setter pattern means wiring order is implicit; a forgotten setter produces a `None`-deref at runtime. Migrating the remaining setters to pass through `initialize_managers()` / route factories extends the existing DI pattern rather than replacing it. Improves testability and reduces startup ordering fragility. | MEDIUM–HIGH | After god-file splits (which will expose constructor injection opportunities). Incremental: each setter eliminated is self-contained. |
| **Migration-system hardening** | 37 hand-rolled `_migrate_*` functions re-scan every table on every boot. Options: (a) minimal — add a `schema_migrations` ledger table and short-circuit already-applied migrations without Alembic; (b) full — introduce Alembic with `alembic stamp head` on the existing schema, then add new migrations via Alembic going forward. Option (a) is lower risk; option (b) enables column drops/renames/type changes in future. Either eliminates the O(N migrations) startup cost. | HIGH | After god-file split of `core/database.py`. Requires careful backward-compat testing: all existing DBs must still load. Schema structure is Out of Scope; migration *mechanism* is in scope. |
| **Structured logging (stdlib → structlog/loguru)** | Currently uses `logging.getLogger(__name__)` per module with string messages. Structured JSON logging (via structlog processor pipeline or loguru + InterceptHandler for Uvicorn/FastAPI) makes request tracing, error attribution, and log aggregation dramatically easier. Adds request-id context propagation through the async call chain. | MEDIUM | After god-file splits (adding structured logging to a 4000-line file is harder). New per-module loggers emerge naturally from the splits. |
| **Broad `except Exception` narrowing** | 1104 `except Exception` handlers and ~250 `except ...: pass` blocks. Not all are wrong — intentional fail-soft degradation is documented. But several DB error paths swallow `OperationalError` and silently report zeroes, masking data loss. Systematic pass: distinguish intentional degradation (comment + keep) from accidental swallowing (narrow + log at WARNING). | MEDIUM | After security audit identifies the highest-risk swallow sites. Can be done incrementally alongside god-file splits. |
| **Orphaned-code cleanup (reverted features)** | Two recent reverts (`67b63e9` prompt bar resize, `1f6c5ac` Codex Agent integration) are on `main`. Verify no dead code, dangling imports, or unreachable configuration remains. Low volume but important for clean baseline. | LOW | After scorecard (includes dead-code scan). Can be done early. |

---

## Anti-Features (Explicitly Out of Scope)

These workstreams were considered and deliberately excluded. The rationale must be clear so
future contributors understand the boundary is intentional.

| Anti-Feature | Why Requested / Tempting | Why Not | What to Do Instead |
|---|---|---|---|
| **Framework migration (FastAPI → anything else)** | Newer frameworks exist; some devs prefer alternatives | Behavior-preserving constraint. Any framework migration risks API contract changes, has long tail of subtle behavioral differences, and provides no user-visible value. | Stay on FastAPI. Modernize the FastAPI usage (DI, lifespan) within the current framework. |
| **Database schema redesign** | 24 tables have grown organically; some could be normalized | Schema changes require data migrations that affect every deployed instance. The table structure is load-bearing for all existing data. Out of Scope per PROJECT.md. | Harden the migration *mechanism* (how schema changes are applied) not the schema *shape*. |
| **API / SSE contract changes** | Endpoints could be RESTier; streaming protocol could use WebSockets | Clients depend on the current HTTP/SSE surface. Breaking changes here require coordinated client updates and are not behavior-preserving. Out of Scope per PROJECT.md. | Leave all HTTP routes, SSE event names, and response shapes identical. |
| **Full frontend framework adoption (React/Vue/Svelte)** | Build tooling would enable bundling, tree-shaking, etc. | Vanilla-JS-to-framework is a major rewrite. No build step is a project feature (easy deployment, no Node dependency in prod). Out of Scope per PROJECT.md. | Split large JS files into focused ES modules. Improve organization without introducing a build step. |
| **Speculative performance re-architecture** | Async DB drivers, Postgres migration, connection pooling | No felt performance pain is documented. Structural changes justified by speculative perf introduce risk without a measurable benefit. Perf is a guardrail (no regressions), not a driver. Out of Scope per PROJECT.md. | WAL/busy_timeout (a correctness fix, not a perf optimization) and startup migration short-circuiting (eliminates a documented startup bottleneck) are in scope because they fix correctness issues. |
| **New product features** | Agent improvements, new integrations, UI enhancements | This is a modernization milestone. New features change the test contract and mix concerns into behavior-preserving refactors, making regressions harder to attribute. Out of Scope per PROJECT.md. | Log feature ideas. Address in a subsequent product milestone after the modernization baseline is locked. |
| **Big-bang rewrites of any subsystem** | Tempting to "do it right" while in a file | A big-bang rewrite of e.g. the tool dispatch or scheduler surfaces cannot be verified incrementally against the existing test suite. History shows reverted big-bang changes on this repo (`1f6c5ac`). | Incremental splits with green tests at each step. Facade pattern for back-compat during transitions. |
| **Alembic adoption as a hard prerequisite** | Full Alembic adoption is the "right" long-term answer | Full Alembic adoption (autogenerate from 24 existing tables, batch migration setup, CI pipeline changes) is a high-complexity undertaking that can block progress on other workstreams. | Treat migration hardening as a differentiator workstream: either the lightweight ledger-table approach (lower risk) or Alembic with `alembic stamp head` brownfield bootstrap — decide at phase planning time based on available effort. |

---

## Workstream Dependencies

```
[Baseline Scorecard]
    └──enables──> [All other workstreams] (provides targets + before-state metrics)

[Ruff Enforcement]
    └──enables──> [Deprecated API Replacement]  (ruff DTZ rule flags all utcnow sites)
    └──enables──> [CI Quality Gates]            (ruff is one of the gate checks)
    └──enables──> [Type-Hint Adoption]          (clean diff baseline)

[Dependency Pinning + Lockfile]
    └──enables──> [CI Quality Gates]            (CI installs from lockfile)
    └──enables──> [Security Audit - CVE scan]   (pip-audit needs pinned deps)

[Type-Hint Adoption]
    └──enables──> [God-File Split (Python)]     (typed code is easier to split safely)

[Test Coverage Baseline + Gap-Filling]
    └──must precede──> [God-File Split (Python)]  (tool_implementations, email_routes)

[CI Quality Gates]
    └──enforces──> [All tooling workstreams going forward]

[God-File Split (Python)]
    └──enables──> [Global Singleton DI Reduction]   (constructor injection emerges from splits)
    └──enables──> [Migration System Hardening]      (core/database.py split first)
    └──enables──> [Structured Logging]              (per-module loggers emerge from splits)
    └──enables──> [Exception Narrowing]             (narrowing is easier in smaller files)

[Security Audit]
    └──informs──> [Exception Narrowing]            (identifies highest-risk swallow sites)
    └──informs──> [Migration System Hardening]     (surfaces SQL construction risks)

[SQLite WAL + busy_timeout]
    └──independent of god-file splits (engine-level config change)
    └──depends on──> [CI Quality Gates]            (DB contention tests need CI to catch regressions)
```

### Key Dependency Notes

- **Scorecard before everything:** Without a measured baseline, there is no definition of done and no way to demonstrate improvement. This runs first, alone.
- **Coverage before splits:** `tool_implementations.py` and `email_routes.py` have no dedicated tests. Splitting them without coverage is blind surgery — a regression would pass CI.
- **Ruff before type hints:** Ruff's annotation rules (`ANN`, `UP`) flag annotation style issues. Running mypy on ruff-clean code removes a class of noise.
- **Lockfile before security audit:** pip-audit and safety require pinned deps to produce meaningful CVE reports.
- **God-file splits before DI reduction and structured logging:** These higher-level improvements are naturally scoped to module boundaries. Introducing DI into a 4000-line god-file makes the diff unreviewable; DI refactors are manageable once the file is already split into focused units.
- **SQLite WAL hardening is independent:** It is an engine-level pragma change. It can be sequenced as an early win after CI gates are established, without waiting for the splits.
- **Frontend splits are independent of Python splits:** No shared ordering constraint. They can be parallelized across teams or phases.

---

## Prioritization Matrix

| Workstream | Value (risk reduction / maintainability) | Implementation Cost | Phase |
|---|---|---|---|
| Baseline scorecard | HIGH | LOW | P1 — Phase 1 |
| Ruff enforcement | HIGH | LOW | P1 — Phase 1 |
| Deprecated API replacement | HIGH | LOW | P1 — Phase 1 |
| Dependency pinning + lockfile | HIGH | LOW–MED | P1 — Phase 1 |
| CI quality gates | HIGH | LOW–MED | P1 — Phase 1 |
| Type-hint adoption (mypy) | HIGH | MEDIUM | P1 — Phase 1/2 |
| Test coverage baseline + gap-filling | HIGH | MED–HIGH | P1 — Phase 2 |
| SQLite WAL + busy_timeout | HIGH | MEDIUM | P1 — Phase 2 (after CI) |
| God-file split (Python) | HIGH | HIGH | P2 — Phase 3 |
| Security audit | HIGH | MED–HIGH | P2 — Phase 3 (after lockfile) |
| Orphaned-code cleanup | MEDIUM | LOW | P2 — Phase 2 (early wins) |
| God-file split (Frontend JS) | MEDIUM | MED–HIGH | P2 — Phase 3/4 |
| Exception narrowing | MEDIUM | MEDIUM | P2 — Phase 3 (alongside splits) |
| Global singleton DI reduction | MEDIUM | MED–HIGH | P3 — Phase 4 (after splits) |
| Migration system hardening | MEDIUM | HIGH | P3 — Phase 4 (after db.py split) |
| Structured logging | MEDIUM | MEDIUM | P3 — Phase 4 (after splits) |

**Priority key:**
- P1: Must have for the milestone to deliver its core value
- P2: Should have, high confidence they fit within the milestone
- P3: Target workstreams — include if capacity allows, otherwise defer to next milestone

---

## Sources

- Project context: `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/ARCHITECTURE.md`
- [Ruff complete guide — pydevtools](https://pydevtools.com/handbook/explanation/ruff-complete-guide/)
- [Modern Python toolkit: Pydantic, Ruff, Mypy, uv — developer-service.blog](https://developer-service.blog/a-modern-python-toolkit-pydantic-ruff-mypy-and-uv/)
- [datetime.utcnow() deprecation and migration — miguelgrinberg.com](https://blog.miguelgrinberg.com/post/it-s-time-for-a-change-datetime-utcnow-is-now-deprecated)
- [SQLite WAL + busy_timeout under concurrent writes — tenthousandmeters.com](https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/)
- [Alembic batch migrations for SQLite — alembic.sqlalchemy.org](https://alembic.sqlalchemy.org/en/latest/batch.html)
- [FastAPI dependency injection patterns — fastapi.tiangolo.com](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [Semgrep vs Bandit: Python security scanning — DEV Community](https://dev.to/rahulxsingh/semgrep-vs-bandit-python-security-scanning-compared-2026-5e5j)
- [uv pip compile / lockfile — docs.astral.sh](https://docs.astral.sh/uv/pip/compile/)
- [Structured logging in FastAPI — apitally.io](https://apitally.io/blog/fastapi-logging-guide)
- [Modular monolith blueprint — strategictech.substack.com](https://strategictech.substack.com/p/modular-monolith-blueprint)

---

*Workstream research for: Odysseus behavior-preserving modernization*
*Researched: 2026-06-03*
