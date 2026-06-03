# Requirements — Odysseus Engineering Modernization

**Milestone:** Behavior-preserving modernization + security audit
**Defined:** 2026-06-03
**Source:** `.planning/PROJECT.md`, `.planning/research/` (STACK, FEATURES, ARCHITECTURE, PITFALLS, SUMMARY), `.planning/codebase/CONCERNS.md`

> **Governing constraint:** Every requirement is behavior-preserving. No feature, HTTP/SSE API,
> schema, or UX change. The existing pytest suite (355 files) is the contract; a requirement is
> only "done" when the suite is green and the relevant scorecard threshold is met.

---

## v1 Requirements

### Baseline & Cleanup (BASE)

- [ ] **BASE-01**: An objective baseline scorecard is captured (per-module line counts, mypy-typed %, ruff finding count, security findings, test-coverage map, startup/key-path perf benchmark) with explicit target thresholds that define milestone "done"
- [ ] **BASE-02**: Orphaned/dead code from the two reverts on `main` (`67b63e9` prompt-bar resize, `1f6c5ac` Codex Agent integration) is audited; dangling imports, unreachable config, and stub code removed

### Tooling & CI Gates (TOOL)

- [ ] **TOOL-01**: `ruff` lint + `ruff format` adopted with config in `pyproject.toml`; repo reaches a clean baseline (auto-fixes applied as standalone commits)
- [ ] **TOOL-02**: `mypy` adopted with gradual/inverted strictness (passes in CI from day 1; god-files relaxed via `[[tool.mypy.overrides]]`; `mcp.*` etc. set `ignore_missing_imports`)
- [x] **TOOL-03**: Dependencies pinned via a committed lockfile (`uv pip compile` or `pip-tools` from a `requirements.in`); CI installs from the lockfile
- [ ] **TOOL-04**: CI pipeline gates every PR on ruff lint, ruff format check, mypy (no regressions), pytest, `bandit`, and `pip-audit`
- [ ] **TOOL-05**: Deprecated `datetime.utcnow()` replaced with timezone-aware equivalents across all 24 files; downstream consumers (CalDAV `UNTIL`, cron comparisons in `task_scheduler`) traced so no new UTC regressions are introduced

### Test Coverage (COV)

- [ ] **COV-01**: A per-file coverage threshold is defined and current coverage is measured and mapped (a file may not be split until it meets the threshold)
- [ ] **COV-02**: Characterization tests are added for the thinly/indirectly covered god-files — `src/tool_implementations.py` and `routes/email_routes.py` first — **before** any split begins on them
- [ ] **COV-03**: Cross-owner isolation tests exist for authenticated data-fetching endpoints, covering the access boundary that route-helper extraction can silently break

### Backend Decomposition (REFAC)

- [ ] **REFAC-01**: `src/tool_implementations.py` (~4100 lines) is decomposed into a package with an `__init__.py` re-export facade (same pattern as `agent_tools.py`); all `do_*` import paths unchanged
- [ ] **REFAC-02**: God-route-modules (`email_routes.py`, `cookbook_routes.py`, `model_routes.py`, `gallery_routes.py`) are decomposed via FastAPI sub-router composition with identical HTTP prefixes, SSE event names, and response shapes
- [ ] **REFAC-03**: Remaining oversized backend modules (`agent_loop.py`, `task_scheduler.py`, `builtin_actions.py`) are decomposed after their coupling/coverage prerequisites are met
- [ ] **REFAC-04**: In-scope backend files meet the agreed module-size heuristic from the scorecard; extractions follow the existing `*_helpers.py` / `setup_*_routes` convention (no new patterns)

### Type Safety (TYPE)

- [ ] **TYPE-01**: Modern type hints (`list[X]`, `X | None`) are added to refactored modules as they are split, raising the mypy-typed % toward the scorecard threshold

### Dependency Injection (DI)

- [ ] **DI-01**: Module-level singleton setter-wiring (`set_session_manager`, `set_task_scheduler`, `set_memory_manager`, …) is migrated to explicit injection via `initialize_managers()` / route factories — extending the existing DI pattern, not `Depends()` for app-lifetime objects; `set_session_manager` (streaming path) migrated last

### Data Layer (DATA)

- [ ] **DATA-01**: SQLite concurrency is verified and hardened — resolve the CONCERNS-vs-ARCHITECTURE conflict by code-read first, then ensure `journal_mode=WAL` + `busy_timeout` + `BEGIN IMMEDIATE` for write transactions; the ~21 raw `sqlite3.connect()` sites are routed through one shared helper; DB-contention tests added
- [ ] **DATA-02**: The migration mechanism is hardened (lightweight `schema_migrations` ledger **or** Alembic `stamp head` brownfield bootstrap — decided at planning) so migrations don't re-scan every table on boot; all existing databases still load unchanged (schema *shape* unchanged)
- [ ] **DATA-03**: DB error paths that silently swallow `sqlite3.OperationalError` (masking data loss) are narrowed and logged at WARNING; intentional fail-soft degradation is kept and commented

### Frontend (FE)

- [ ] **FE-01**: Frontend god-files (`document.js` ~9700 lines, `slashCommands.js` ~6100, `emailLibrary.js` ~5200, `notes.js` ~5000) are split into focused ES modules with **no build step / framework**; SPA behavior and appearance are unchanged

### Security Audit (SEC)

- [ ] **SEC-01**: A systematic OWASP ASVS L1 audit is performed across auth, per-owner scoping, input handling, and secret storage — extending `THREAT_MODEL.md` / `SECURITY.md` — producing a triaged findings list with an endpoint-enumeration checklist
- [ ] **SEC-02**: A dependency CVE scan (`pip-audit`) runs against the lockfile; findings are triaged and high-severity CVEs resolved
- [ ] **SEC-03**: Dynamic SQL built via f-string interpolation (in `core/database.py` / `routes/`) is reviewed and parameterized where it carries injection risk
- [ ] **SEC-04**: Secret-storage handling is reviewed and hardened/documented — including the encryption-key co-location with data (`src/secret_storage.py`) and fail-soft `decrypt` behavior
- [ ] **SEC-05**: `bandit` and `semgrep p/fastapi` are run once as an audit pass; findings are triaged into a curated config and high-severity issues are closed

---

## v2 Requirements (Deferred — capacity-permitting or next milestone)

- [ ] **LOG-01**: Migrate stdlib `logging` to structured logging (structlog/loguru + Uvicorn intercept) with request-id context propagation — a P3 differentiator that emerges naturally once god-files are split
- [ ] **EXC-01**: Systematic narrowing of the broad `except Exception` / `except: pass` blocks (1104 / ~250 occurrences) beyond the DB paths covered by DATA-03
- [ ] **FE-02**: Adopt `eslint` (flat config) + `prettier` for `static/` and gate in CI (requires verifying the Docker Node version ≥ 20.19)
- [ ] **DATA-04**: Full Alembic adoption (if DATA-02 chose the lightweight ledger) for future column drops/renames/type changes

---

## Out of Scope

<!-- Explicit exclusions with reasoning — mirrors PROJECT.md. -->

- **New product features** — this is a modernization milestone; features change the test contract and obscure regression attribution
- **API / SSE contract changes** — clients depend on the current surface; changes aren't behavior-preserving
- **UX / visual redesign** — SPA behavior and appearance are preserved; only internal JS structure changes
- **Database schema redesign / data migration** — table *shape* is load-bearing for existing data; only the migration *mechanism* is in scope
- **Framework migration (FastAPI → other) or frontend framework adoption (React/Vue/Svelte/build step)** — major rewrites with no user-visible value; vanilla-JS no-build is a deliberate project feature
- **Speculative performance re-architecture** (async DB driver, Postgres, pooling) — no felt perf pain; perf is a guardrail, not a driver. (WAL/busy_timeout and migration short-circuiting are in scope as *correctness* fixes, not perf optimizations)
- **Big-bang rewrites of any subsystem** — cannot be verified incrementally against the suite; repo history shows reverted big-bang changes (`1f6c5ac`)

---

## Traceability

<!-- Phase mapping filled in by the roadmapper — 2026-06-03. -->

| REQ-ID | Phase | Status |
|--------|-------|--------|
| BASE-01 | Phase 1 | Pending |
| BASE-02 | Phase 1 | Pending |
| TOOL-01 | Phase 1 | Pending |
| TOOL-02 | Phase 1 | Pending |
| TOOL-03 | Phase 1 | Complete (01-01) |
| TOOL-04 | Phase 1 | Pending |
| TOOL-05 | Phase 3 | Pending |
| COV-01 | Phase 2 | Pending |
| COV-02 | Phase 2 | Pending |
| COV-03 | Phase 2 | Pending |
| REFAC-01 | Phase 3 | Pending |
| REFAC-02 | Phase 3 | Pending |
| REFAC-03 | Phase 3 | Pending |
| REFAC-04 | Phase 3 | Pending |
| TYPE-01 | Phase 3 | Pending |
| DI-01 | Phase 4 | Pending |
| DATA-01 | Phase 2 | Pending |
| DATA-02 | Phase 4 | Pending |
| DATA-03 | Phase 4 | Pending |
| FE-01 | Phase 4 | Pending |
| SEC-01 | Phase 5 | Pending |
| SEC-02 | Phase 5 | Pending |
| SEC-03 | Phase 5 | Pending |
| SEC-04 | Phase 5 | Pending |
| SEC-05 | Phase 5 | Pending |

---

*Requirements for: Odysseus behavior-preserving modernization — defined 2026-06-03*
