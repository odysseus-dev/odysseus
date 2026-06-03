# Pitfalls Research

**Domain:** Large Python/FastAPI monolith modernization + security audit (behavior-preserving)
**Researched:** 2026-06-03
**Confidence:** HIGH — grounded in Odysseus's actual CONCERNS.md, ARCHITECTURE.md, TESTING.md, and verified against
community post-mortems and official documentation.

---

## Critical Pitfalls

### Pitfall 1: Refactoring Before Coverage Exists on the Target File

**What goes wrong:**
`src/tool_implementations.py` (4144 lines) and `routes/email_routes.py` (3214 lines) are confirmed to have *no
dedicated test files*. Their coverage arrives only indirectly through helper tests and integration-level tests
that call them via the full route stack. A team member splits one of these god-files into five focused modules,
runs the full pytest suite, gets green — and ships a behavior regression that only shows up in production because
the indirect coverage never exercised the exact branch that moved.

**Why it happens:**
Green-suite is the agreed behavior contract (PROJECT.md), so teams conflate "suite passes" with "all behaviors
covered." Indirect tests exercise the happy path but leave branches, error paths, and edge cases dark. Splitting
moves code to a new location; any test that was incidentally hitting the original code via side-entry now silently
misses the refactored branch.

**How to avoid:**
Treat every god-file as a coverage prerequisite, not a refactoring target. Before touching `tool_implementations.py`
or `email_routes.py`, run `pytest --cov=src/tool_implementations --cov=routes/email_routes --cov-report=term-missing`
to produce a line-by-line gap map. Add direct tests (following the existing `test_<subject>.py` convention) until
critical branches — error handling, auth checks, all tool dispatch arms — are covered. Only then start extracting
modules. This is already noted as a Key Decision in PROJECT.md ("Fill coverage gaps before refactoring thinly-tested
god-files") — the pitfall is teams skipping it under schedule pressure.

**Warning signs:**
- No `tests/test_tool_implementations*.py` or `tests/test_email_routes*.py` files exist (confirmed in CONCERNS.md).
- A god-file split PR does not include new test files.
- Coverage report shows `tool_implementations.py` or `email_routes.py` in the "missing" column only through indirect
  imports.
- The PR touches >200 lines of production code and has fewer lines of new tests.

**Phase to address:**
Phase 1 (Baseline scorecard + coverage gap fill) — before any god-file split begins. The coverage gap fill is its
own workstream gate; no split PR should be merged until the target file's direct coverage clears a threshold agreed
in the scorecard.

---

### Pitfall 2: Behavior Drift via Stub-Mismatch After Module Split

**What goes wrong:**
The test suite stubs heavy dependencies at import time — both in `conftest.py` (global stubs for `sqlalchemy`,
`bcrypt`, `httpx`, etc.) and per-file via `sys.modules` injection before import. When `tool_implementations.py` is
split into a package (`src/tools/`), each sub-module gets its own import path. Existing tests that stub
`src.tool_implementations.some_dep` now point at the old path; the new sub-module imports the real dep or a
different stub, silently changing behavior. The suite stays green because the stub was just bypassed rather than
removed.

**Why it happens:**
Stub-by-path is fragile across refactors. `sys.modules['src.tool_implementations'] = MagicMock()` bypasses the
original module entirely; after a split, `src.tools.email` imports `httpx` directly and the stub no longer applies.
This is a class of behavior drift that is invisible to line-coverage metrics.

**How to avoid:**
Before splitting a god-file, audit every test that stubs it or its dependencies. Ensure stubs are applied at the
dependency's canonical path (`httpx`, `sqlalchemy`) not the consuming module's import alias. After the split, run
a diff of `sys.modules` patches across all tests and confirm no path is now dangling. The existing `importlib.util`
pattern used in `tests/test_atomic_io.py` (direct file load) is safer than path-based stubs for unit-level tests
on newly created submodules.

**Warning signs:**
- After a module split, `git grep 'sys.modules.*tool_implementations'` finds stubs pointing at the old module path.
- A test that previously tested an error branch now passes trivially (the stub is bypassed, not the real code path).
- Behavior assertions change to "never called" after a split where they were previously "called once."

**Phase to address:**
Phase 2 (God-file splits) — as a pre-merge checklist item on every split PR. Add a CI step that `grep`s for
deprecated stub paths post-rename.

---

### Pitfall 3: Implicit Import Order Breaks Module-Level Singletons

**What goes wrong:**
`app.py` wires all managers at module load time (`initialize_managers()` is called at lines 154-596). Several
modules also use setter-based wiring (`set_session_manager`, `set_task_scheduler`, `set_memory_manager`). When a
god-file is split into a package with an `__init__.py`, Python executes the `__init__.py` at package import time —
before the setter-wired dependencies have been injected by `app.py`. Code in a sub-module that calls
`from src import task_scheduler` at module level now runs before `set_task_scheduler()` fires, yielding `None`
refs that crash only at first use, not at startup.

In Odysseus, many functions use lazy/local imports (noted in ARCHITECTURE.md) precisely to break this exact class
of cycle. Splitting god-files without preserving that lazy-import pattern inside the new sub-modules reintroduces
the initialization ordering problem the pattern was designed to prevent.

**Why it happens:**
Refactoring tools and developers familiar with eager-import style naturally hoist imports to file top-level. The
lazy-import-as-cycle-breaker convention is non-obvious and undocumented at the call site; without explicit guidance
it gets lost in a split.

**How to avoid:**
Document the lazy-import convention in the refactoring guide or a `CONVENTIONS.md` file before splits start. When
creating new sub-modules, audit each function for module-level imports of `src.*` singletons and keep them as
local imports until the full DI reduction is done. Test that `python -c "import src.tools.email"` (or whichever
new package) does not blow up with `AttributeError`/`None` when run before `app.py`'s `initialize_managers()`.

**Warning signs:**
- A new sub-module `__init__.py` has top-level `from src import task_scheduler` or `from app import ...`.
- `AttributeError: 'NoneType' object has no attribute '...'` appearing only on first request after startup, not
  during import.
- The test for a new sub-module fails when run in isolation but passes in the full suite (because `conftest.py`
  sets up singletons globally).

**Phase to address:**
Phase 2 (God-file splits) — create a pre-split checklist: (a) identify all module-level singleton accesses in the
target file, (b) confirm each is a local import or is passed through DI. This is also the phase that begins
reducing setter-wiring in favor of explicit DI — do not get ahead of that work.

---

### Pitfall 4: Scope Creep from Refactor into Redesign

**What goes wrong:**
A developer splitting `routes/email_routes.py` notices the underlying business logic is tangled and starts
restructuring it into a proper service layer — introducing `services/email/service.py`, new Pydantic models, and
a different calling convention. This is a redesign, not a refactor. The existing tests don't cover the new
calling convention; the HTTP API is nominally unchanged but the internal behavior is different; and the PR is
now 2000 lines, unreviable, and blocks the milestone.

**Why it happens:**
God-files are often genuinely bad code. Once a developer is inside one, the refactoring energy naturally wants
to fix the underlying design. PROJECT.md is explicit that this milestone is behavior-preserving, not redesign —
but that boundary is easy to drift past when you have your hands on the code.

**How to avoid:**
Enforce a strict rule: a split PR moves code to new file locations *without changing call signatures, return shapes,
or behavior*. Use the facade re-export pattern (already established: `agent_tools.py` re-exports four submodules)
— the original module becomes a thin re-export shell so all callers continue to work without changes. Any API
or convention improvement is logged as a follow-on issue and deferred to a subsequent milestone. Apply a PR size
limit (e.g., max 500 lines of net production-code change per split PR).

**Warning signs:**
- A split PR introduces new classes, new Pydantic models, or new helper signatures that callers must adopt.
- The PR description says "while I was in there I also…"
- Function call signatures change in the split PR rather than only file location.
- New `services/` directories appear mid-split before the scope of the split is complete.

**Phase to address:**
Phase 2 (God-file splits) — enforce in the PR template: "Does this PR change any calling convention, return type,
or observable behavior? If yes, it must be split into a separate PR."

---

### Pitfall 5: SQLite `OperationalError: database is locked` Silently Swallowed

**What goes wrong:**
CONCERNS.md confirms: no `PRAGMA journal_mode=WAL` and no `PRAGMA busy_timeout` are currently set. The app has
concurrent writers: FastAPI route handlers, the email poller, the task scheduler, and the cleanup service all
write to the same SQLite file. Under any real concurrency, `sqlite3.OperationalError: database is locked` fires.
Several code paths already swallow this: `routes/task_routes.py:476` catches `sqlite3.OperationalError` and sets
a count to 0, silently dropping work. With no WAL and no timeout, the probability of this error rises
non-linearly with background-task frequency.

Additionally, there are 21 ad-hoc `sqlite3.connect()` call sites scattered through `core/database.py`,
`routes/email_pollers.py`, `routes/task_routes.py`, and `mcp_servers/email_server.py`. These raw connections
bypass the SQLAlchemy engine's event listener that would set any pragmas, so even after WAL is added to the
engine they remain unconfigured.

**Why it happens:**
SQLite's default journal mode is DELETE (rollback journal). `check_same_thread=False` suppresses the Python-level
thread check but does not enable concurrent writes. A single async event loop with background pollers issues
overlapping write transactions; the lock contention is intermittent (depends on timing), so it passes all unit
tests and only manifests under realistic concurrent load.

**How to avoid:**
1. Add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000` (5 seconds) in the `set_sqlite_pragma` event
   listener in `core/database.py:46`. This covers all SQLAlchemy `SessionLocal()` connections.
2. Create a single shared helper function (e.g., `core/database.py::raw_sqlite_connect(path)`) that wraps
   `sqlite3.connect()` and applies the same pragmas. Route all 21 call sites through it.
3. Change all raw `sqlite3.connect()` contexts that run a write transaction to use `BEGIN IMMEDIATE` (not `BEGIN`)
   to avoid read-to-write lock upgrade races. SQLite ignores `busy_timeout` on lock-upgrade failures regardless
   of the timeout setting.
4. Narrow the `except sqlite3.OperationalError: count = 0` at `task_routes.py:476` — at minimum log at ERROR
   level with the query context so silent data loss is visible.

**Warning signs:**
- `grep -r 'sqlite3.connect(' src/ routes/ core/ mcp_servers/` returns more than 1 result (any non-helper call
  site means pragma config is bypassed).
- `grep -r 'OperationalError' routes/` finds bare `except ... pass` or `count = 0` assignments.
- The test suite contains no test that exercises two concurrent writers against the DB (confirmed missing in
  CONCERNS.md).
- Application logs show `database is locked` only under email polling load.

**Phase to address:**
Phase 3 (Database hardening) — this is the highest-risk fragile area with confirmed production impact potential.
Do the pragma fix and helper consolidation as the first action in this phase, before any migration work, because
the 21 raw connection sites also need to be touched to introduce a migration ledger.

---

### Pitfall 6: `datetime.utcnow()` Replacement Creating New Naive/Aware Mixing Bugs

**What goes wrong:**
24 files use `datetime.utcnow()`. The naive mechanical fix is `s/datetime.utcnow()/datetime.now(timezone.utc)/g`.
But downstream consumers of those datetimes — comparison operators, DB serialization, ICS/CalDAV format strings,
cron recurrence logic in `src/task_scheduler.py` — may assume naive datetimes. Replacing the *producer* without
auditing the *consumer* creates a new class of `TypeError: can't compare offset-naive and offset-aware datetimes`
or, worse, silent miscalculation where `.replace(tzinfo=None)` is used defensively.

CONCERNS.md notes this area has already produced multiple regressions (calendar recurrence UTC fix
`bfbbc9b #1383`, and similar). A bulk find-replace that does not trace the value through its full lifecycle
will create new regressions in the same area.

**Why it happens:**
`datetime.utcnow()` is easy to find and replace; the consumers that expect naive datetimes are scattered and
harder to find. Schedulers are particularly dangerous because: (a) they compare stored datetimes against current
time to decide what to run, (b) aware datetimes format differently in ISO strings, and (c) CalDAV `UNTIL` fields
have format requirements that differ between naive and aware.

**How to avoid:**
For each call site:
1. Trace the value forward to find all downstream comparisons, serializations, and format operations.
2. Identify whether the downstream site requires naive or aware datetime.
3. Replace producer and fix all downstream consumers in the same commit.
4. Add or update a regression test that verifies the specific comparison/serialization doesn't drift — model on
   `tests/test_calendar_rrule_until_utc.py` which already pins this pattern.
Do not do a bulk mechanical replace; treat each call site as its own small PR with targeted tests.

**Warning signs:**
- A `utcnow()` replacement PR does not touch any tests in `tests/test_calendar*` or `tests/test_task_scheduler*`.
- After replacement, `grep -r '\.replace(tzinfo=None)'` appears in any code that was not already there — a sign
  that a consumer was patched around rather than properly fixed.
- CI passes but a manual test of a recurring calendar event shows it fires one hour off.

**Phase to address:**
Phase 4 (Deprecated API cleanup) — own the full file-by-file audit. The calendar/scheduler cluster (`src/task_scheduler.py`,
`src/caldav_sync.py`, `routes/email_pollers.py:983`) is the highest-risk sub-set and should be done first with
dedicated regression tests before the lower-risk utility files.

---

### Pitfall 7: Auth/Owner-Scoping Regression During Route Splits

**What goes wrong:**
`routes/email_routes.py` (3214 lines) contains per-route `owner_filter` checks mixed in with the bulk of the
handler logic. When splitting this into focused modules, it is easy to move the business logic to a helper
function without verifying that the `owner` parameter (or the `current_user` dependency) is still threaded
through correctly. The resulting route appears to work — it returns the right data for the calling user — but
a different user who shares the same server can now access another user's emails or tasks.

This is the single most dangerous class of regression in a behavior-preserving refactor on a multi-user system.
Odysseus has `tests/test_security_regressions.py` specifically to pin prior auth fixes, but it cannot cover
*new* routes that did not exist as separate code paths before the split.

**Why it happens:**
In a god-file, the `owner` variable is in scope for the entire handler closure. When logic is extracted to a
standalone function, the developer must explicitly pass `owner` as a parameter. Missing one extraction point
compiles cleanly, has no type error (if `owner` is `str`), and passes all current tests because no existing
test exercises cross-user access on the newly extracted path.

**How to avoid:**
For every function extracted from a route module: (a) confirm `owner` or `current_user` is an explicit parameter,
not inherited from closure scope; (b) add a test that calls the function with a *different* owner than the
fixture data and asserts it returns empty/404, not the other user's data. Follow the model in
`tests/test_security_regressions.py`. Consider a static analysis step: `grep` any new helper function defined
outside a route factory for missing `owner` parameter on functions whose name suggests data access
(`get_*`, `fetch_*`, `load_*`, `list_*`).

**Warning signs:**
- A split PR's helper functions access DB models without an `owner=` parameter.
- New `services/` or `*_helpers.py` functions query e.g. `EmailAccount.query.filter_by(user_id=...)` but the
  `user_id` comes from a closure rather than a parameter.
- `tests/test_security_regressions.py` has no new test cases in the same PR as a route split.

**Phase to address:**
Phase 2 (God-file splits) — mandatory: every route-extraction PR must include at least one cross-owner access
test for each extracted data-fetching function. Add this to the split PR template.

---

### Pitfall 8: mypy Strictness Applied Too Aggressively Too Fast

**What goes wrong:**
`mypy --strict` on an untyped 114K-line codebase produces hundreds of errors on first run. Teams either: (a)
add a blanket `# type: ignore` on every file to get CI green (defeating the purpose), or (b) spend weeks
fixing `Any` chains before any refactoring ships (momentum collapse). Neither outcome is acceptable for a
modernization milestone that has other co-equal goals.

Additionally, Odysseus uses lazy local imports (`from src.settings import get_setting` inside function bodies)
as an established cycle-breaking pattern. `mypy --strict` combined with `--disallow-untyped-calls` flags every
lazy-imported call as potentially `Any`-returning, producing hundreds of false-positive errors in code that
should not be touched.

**Why it happens:**
The intuition "turn on strict mode and fix everything" is correct for greenfield but catastrophic for brownfield.
Strict mode is a composite of ~15 individual flags; enabling them all simultaneously creates an unworkable
error volume.

**How to avoid:**
Use a per-module, incremental flag strategy:
1. Start with `mypy --ignore-missing-imports --no-strict-optional` — zero-error baseline.
2. Enable one flag at a time (e.g., `--disallow-untyped-defs` in the new sub-modules only, not globally).
3. Use `[[tool.mypy.overrides]]` in `pyproject.toml` to apply stricter checks only to newly-split modules.
4. Reserve `--strict` for modules that are fully typed (expected: end of milestone, not beginning).
5. Never add a blanket `# type: ignore` without a comment; use `# type: ignore[specific-code]` and treat each
   occurrence as a tracking issue.

**Warning signs:**
- `pyproject.toml` `[tool.mypy]` has `strict = true` globally from day one.
- `grep -r '# type: ignore$'` (without a code) shows more than a few dozen occurrences within one sprint.
- mypy is only enabled on new files, never on existing files — means the ratchet is not closing.

**Phase to address:**
Phase 1 (Baseline scorecard) — set up mypy with the zero-error baseline config and document the current
`Any`-coverage percentage as a tracked metric. Phase 2+ modules get incrementally stricter overrides as they
are created.

---

### Pitfall 9: ruff Rule Churn Breaking CI on Rule Upgrades

**What goes wrong:**
ruff is fast-moving with 900+ rules. Enabling a broad rule set (e.g., `select = ["ALL"]`) or enabling rules
in `preview` mode means a ruff version bump in CI turns previously clean code into a failing build — not because
the code changed, but because a new or changed rule now fires. Teams either skip the ruff upgrade (tool rots)
or scramble to add `noqa` directives that clutter the codebase.

**Why it happens:**
`preview` rules are explicitly unstable and change between releases. Using `select = ["ALL"]` opts into every
future rule automatically including preview rules. For a brownfield codebase with existing style, this generates
hundreds of new findings on every ruff bump.

**How to avoid:**
Pin ruff to an explicit version in `requirements.txt` (and later the lockfile). Start with a conservative,
explicit rule set: `select = ["E", "F", "W", "I", "UP"]` (pycodestyle errors, pyflakes, warnings, isort, pyupgrade).
Do not use `preview = true` in CI gates. Add rules one at a time as the codebase cleans up. When bumping ruff,
run `ruff check --diff` to preview new violations before the bump lands in CI.

**Warning signs:**
- `pyproject.toml` `[tool.ruff.lint]` has `select = ["ALL"]` or `preview = true`.
- A ruff version bump PR has more `noqa` additions than code fixes.
- CI fails on a branch that has no Python changes (ruff bumped in a dependency update).

**Phase to address:**
Phase 1 (Tooling adoption, CI setup) — lock ruff version and establish the minimal stable rule set before
enabling CI enforcement. Expand rules in Phase 2+ as part of each refactoring wave.

---

### Pitfall 10: Dependency Pinning Breaking Transitive Deps or ML Stack

**What goes wrong:**
`requirements.txt` is currently unpinned. A naive `pip freeze > requirements.lock` captures the current
installed state — but if this is done in a dev environment with a different Python minor version or OS than
CI/production, the locked hashes may not resolve for another platform. More critically: `chromadb-client`,
`fastembed`, and `mcp` are fast-moving. Pinning `chromadb-client==X.Y.Z` may pull in a transitive dep version
that is incompatible with a separately pinned `fastembed` or `onnxruntime`.

**Why it happens:**
Python's `requirements.txt` pin format is not a full lockfile — it records top-level pins without resolving the
full transitive graph. `pip freeze` records everything currently installed, but that graph is not guaranteed to
be reproducible on a clean install on a different platform.

**How to avoid:**
Use `uv` or `pip-compile` (from `pip-tools`) to generate a full dependency graph with hashes. Do the initial
pin on a clean Docker environment that matches CI. Validate the lockfile with a `pip install --dry-run` on a
second clean environment before committing. Add the chromadb/fastembed/mcp group as a separate optional
`requirements-ml.txt` with its own pins so the core app can be installed without the heavyweight ML stack
(which also validates the graceful-degradation fallback path — see CONCERNS.md).

**Warning signs:**
- `pip install -r requirements.lock` fails with `ResolutionImpossible` on a clean Python environment that
  differs from the one used to generate the lock.
- `chromadb-client` and `fastembed` are pinned to exact versions but no `onnxruntime` pin is present (the
  transitive dependency that most commonly creates conflicts).
- The lockfile was generated on a Mac but CI runs on Linux (wheel availability differs).

**Phase to address:**
Phase 1 (Baseline scorecard) — generate the initial lockfile as part of the baseline tooling setup. Validate
it on the CI environment before any other phase begins.

---

### Pitfall 11: Flaky Async Tests Under Coverage Instrumentation

**What goes wrong:**
`asyncio_mode = "auto"` means all `async def test_*` functions run against a shared event loop policy.
When `pytest-cov` is added (currently not a declared dependency per TESTING.md), coverage instrumentation
wraps coroutines and generators, altering timing. SSE streaming tests and tests that use `asyncio.create_task`
can fail non-deterministically because coverage's tracing hooks introduce enough latency to trigger race
conditions in async generators or task cleanup.

Additionally, Odysseus uses background tasks stored in `app.state._startup_tasks` to prevent GC. Tests that
import or mock `app` may trigger startup tasks that run against a test event loop and conflict with the
coverage-instrumented loop, producing `RuntimeError: This event loop is already running` or orphaned tasks
that cause teardown warnings to become failures.

**Why it happens:**
Coverage instrumentation is transparent for synchronous code but intrusive for async code — it patches
`sys.settrace` in ways that interact poorly with `asyncio`'s internal tracing. `asyncio_mode = "auto"` uses
a per-test event loop by default in newer `pytest-asyncio` versions, but class-scoped or module-scoped
fixtures can leak loop state.

**How to avoid:**
Run coverage with `--no-cov` first to confirm the base suite is stable. When adding coverage, use
`pytest-cov` with `--cov-config=pyproject.toml` and set `concurrency = "greenlet,thread"` in the `[coverage:run]`
section. For async streaming tests, add a small `asyncio.sleep(0)` yield after task creation to allow the
event loop to settle before asserting. If flakiness persists in specific tests, mark them with
`@pytest.mark.flaky(reruns=2)` temporarily and file a tracking issue rather than suppressing with `try/except`.

**Warning signs:**
- A test that passes in isolation fails when run in the full suite (event loop state leakage).
- Tests that test SSE streaming or `asyncio.create_task` fail only when `--cov` flag is present.
- `RuntimeError: This event loop is already running` in teardown.
- Intermittent `asyncio.CancelledError` in tests that don't explicitly cancel anything.

**Phase to address:**
Phase 1 (Baseline + CI setup) — run coverage baseline before any refactoring begins to confirm the suite
is stable under instrumentation. Async test stability is a prerequisite for trusting the test suite as a
behavior contract.

---

### Pitfall 12: False Security Confidence from the Existing Test Suite

**What goes wrong:**
`tests/test_security_regressions.py` is an excellent start, but it pins *previously discovered* vulnerabilities —
it does not provide systematic coverage of auth surfaces. The security audit's risk is that it runs against
the existing test suite, finds no failures, and declares the auth surface safe. Missing: direct tests for
cross-owner data isolation on endpoints that were never explicitly tested for it (confirmed gap for
`tool_implementations.py` and `email_routes.py` surfaces).

Concretely: an attacker who can register two accounts and make cross-user API calls would find endpoints
that were never explicitly tested for per-owner scoping — even though the app *intends* per-owner isolation.

**Why it happens:**
Security regression tests are written reactively (after a bug is found). Systematic pre-audit testing of
all auth-guarded endpoints requires enumerating *every* authenticated endpoint and writing a test that
asserts cross-owner access returns 403/empty — work that was never done.

**How to avoid:**
Before declaring the security audit complete: enumerate all `setup_*_routes` factories, list every endpoint
that touches user data, and for each one confirm a cross-owner test exists (or write it). Use
`fastapi.testclient.TestClient` with two different auth credentials. Add this enumeration to the scorecard
as a tracked metric: "N routes with cross-owner isolation tests / M total authenticated data routes."

**Warning signs:**
- The security audit result is "we ran the test suite and it passed."
- `tests/test_security_regressions.py` has not grown during the audit phase.
- Cross-owner access tests are absent for any of the five god-files being split.

**Phase to address:**
Phase 5 (Security audit) — but start the enumeration list in Phase 1 (scorecard baseline) so the audit phase
has a pre-built checklist to work from.

---

### Pitfall 13: Secret Handling Regression from Key Co-Location

**What goes wrong:**
`src/secret_storage.py` stores the Fernet key at `data/.app_key` — next to `data/app.db`. CONCERNS.md notes
the current mitigation (chmod 0o600). The refactoring milestone touches `core/database.py` heavily (migration
hardening). A change that restructures the `data/` path configuration or moves the DB path could silently
break the relative key-path resolution, causing `decrypt()` to fail-soft and return `""` for all secrets.
All email/calendar credentials then silently become empty strings — the app boots and the tests pass, but
every external integration is broken at runtime.

**Why it happens:**
`decrypt()` is designed to fail-soft (return `""`) to prevent a corrupt/rotated key from crashing the app.
That same fail-soft behavior hides a path regression. No test currently exercises the "key found, correct
decryption succeeds" path end-to-end; tests mock the encryption layer.

**How to avoid:**
When any change touches `core/database.py`'s path configuration or the `data/` directory structure:
1. Add a test that exercises the full Fernet encrypt/decrypt round-trip (not mocked) against a temp directory.
2. Assert that `decrypt(encrypt(value)) == value` — i.e., that the key is found and functional, not just
   that the function doesn't raise.
3. Add a startup health check that verifies key accessibility and logs `ERROR` (not silently degrades) if the
   key is missing.

**Warning signs:**
- Email accounts are all showing as unauthenticated after a `database.py` refactor.
- `decrypt()` returns `""` for values that were previously non-empty (check via a manual DB inspection script
  or startup log).
- `grep -r '_KEY_PATH'` shows only a hardcoded relative path with no environment-variable override.

**Phase to address:**
Phase 3 (Database hardening) — any path refactoring in this phase must include a decrypt round-trip test.
The env-var override for the key path (flagged in CONCERNS.md) is also a Phase 3 task.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| `# type: ignore` without a code | Silences mypy fast | Permanently hides type errors; can't track or fix systematically | Never — always use `# type: ignore[code]` with a comment |
| Global `select = ["ALL"]` in ruff config | Comprehensive linting immediately | Every ruff version bump breaks CI; hundreds of `noqa` suppressions accumulate | Never for CI gate; acceptable for one-off audits |
| `pip freeze` from dev env as lock | Quick pin of current state | Platform-specific wheels break on CI/prod; transitive graph not guaranteed reproducible | Never — use `pip-compile` or `uv lock` on a clean CI-equivalent environment |
| Splitting god-file without adding tests first | Faster iteration on split | Behavior drift is invisible; split PR cannot be safely reviewed | Never — coverage gate must exist before split starts |
| `except Exception: pass` on new code | Prevents crashes during refactor | Swallows real errors; debug is near-impossible; masks the SQLite locking issue | Only for graceful-degradation of *optional* subsystems, and only with a `logger.warning` |
| Keeping stub paths pointing at pre-split module names | Tests continue to pass after split | Stubs no longer match real import paths; false test coverage | Never — update stub paths in the same commit as the split |
| Adding `PRAGMA journal_mode=WAL` only to the SQLAlchemy engine | Quick fix for the main writer | 21 raw `sqlite3.connect()` sites still use DELETE journal, still hit lock contention | Never — the pragma helper must cover all connection sites |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| SQLite WAL mode | Adding `PRAGMA journal_mode=WAL` only in the SQLAlchemy event listener | Apply via a `raw_sqlite_connect()` helper used by all 21 raw call sites too |
| SQLite busy_timeout | Using `busy_timeout=5000` but keeping `BEGIN` for write transactions | Use `BEGIN IMMEDIATE` for any transaction that will write; `busy_timeout` does not help read-to-write upgrades |
| mypy + lazy local imports | Running `--strict` globally, triggering errors on every local import | Use per-module `[[tool.mypy.overrides]]` to keep new modules strict and existing lazy-import modules lenient |
| CalDAV datetime | Replacing `utcnow()` in caldav_sync without checking `UNTIL` field format requirements | Audit ICS `UNTIL` format constraints — some CalDAV servers require `Z`-suffix UTC strings; aware datetimes format differently |
| ruff + pre-commit | Pinning ruff in pre-commit config but not in requirements.txt | Keep ruff version identical between pre-commit, CI, and requirements/lockfile |
| pip-audit in CI | Running `pip-audit` without a `.pip-audit-ignore` file | Pre-populate with `--ignore-vuln` for known non-applicable CVEs (e.g., test-framework false positives) to avoid alert fatigue |
| coverage + async | Using `pytest-cov` with default `concurrency` setting | Set `concurrency = "greenlet,thread"` and validate the suite is stable before enabling CI gate |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| 37 startup migrations re-scanning all tables on every boot | Slow startup; grows with each new migration | Add a `schema_migrations` ledger table; short-circuit applied migrations | Already a problem at 37 migrations; gets worse with each addition |
| mypy running on all 560 files on every CI push | CI time grows to minutes; developers skip type checking locally | Run mypy only on changed files + direct dependents (incremental mode via `mypy --incremental`) | Noticeable after ~100 typed files; painful at full codebase scale |
| Coverage running on all 355 tests to validate one changed module | Test cycle time grows; developers run tests less frequently | Use `pytest --cov=<specific-module>` for local work; full coverage only in CI | At current 355 test files, full coverage run is already slow without `--cov` |
| Raw sqlite3 connections without connection pooling | Connection overhead under scheduler tick frequency | Route all connections through SQLAlchemy engine which handles pooling | Under high-frequency scheduled tasks (sub-second ticks) |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Route split without re-testing cross-owner isolation | One user reads another user's data | For every extracted data-access helper, write a cross-owner test that asserts empty/403 |
| Encryption key path broken by DB path refactor | All secrets decrypt to `""`; all integrations silently fail | Round-trip test `encrypt/decrypt` in any PR that touches `core/database.py` path config |
| `_safe_ident()` allow-list not implemented for f-string SQL | Schema-derived identifiers could be confused with user input if the allow-list assumption is ever violated | Implement the `_safe_ident()` helper called out in CONCERNS.md; add a linting rule that flags f-string SQL |
| `pip-audit` not in CI | CVEs introduced by dependency pin not detected | Add `pip-audit` as a CI step; run against the lockfile; treat high/critical CVEs as build blockers |
| Reverted PRs leaving orphaned dead code | Orphaned code from `1f6c5ac` (Codex Agent) may contain security-relevant stubs or credentials | Audit reverted PRs' diffs for orphaned files; `git diff main..revert-commit -- '*'` confirms cleanup |

---

## "Looks Done But Isn't" Checklist

- [ ] **WAL + busy_timeout:** `PRAGMA journal_mode=WAL` appears in `core/database.py` event listener AND a
  `raw_sqlite_connect()` helper used by all 21 raw call sites — verify with `grep -r 'sqlite3.connect(' src/ routes/ core/ mcp_servers/`
  returns only the helper function definition.
- [ ] **datetime.utcnow() replaced:** Not just the call site but all downstream comparisons and serializations
  audited — verify with `grep -rn 'utcnow' .` returning 0 results, and confirm no new `.replace(tzinfo=None)` appeared.
- [ ] **God-file split complete:** Not just the new files created but the original module kept as a re-export
  facade so zero callers were broken — verify with `python -c "from src.tool_implementations import <all_public_names>"` passing.
- [ ] **Coverage gate active in CI:** Not just "coverage runs" but a threshold is enforced and a failing build
  occurs when it drops — verify a test deletion causes CI to fail.
- [ ] **Dependency lockfile platform-validated:** Not just `pip freeze` output but verified reproducible on a
  clean Linux Docker image matching CI — verify with `docker run --rm python:3.12-slim pip install -r requirements.lock`.
- [ ] **Security audit complete:** Not just "tests pass" but every authenticated endpoint enumerated and
  cross-owner access tested — verify the enumeration list in the scorecard has 100% test coverage.
- [ ] **mypy ratchet closing:** Not just "mypy runs" but the `Any`-coverage percentage metric in the scorecard
  is trending down sprint-over-sprint — verify with `mypy --stats` output logged in CI.
- [ ] **ruff version pinned:** ruff version in `pyproject.toml` pre-commit config matches `requirements.lock`
  — verify `pre-commit run ruff` uses identical rule behavior to CI.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| God-file split shipped without direct test coverage | HIGH | Revert the split, add direct tests first, re-split; do not attempt to retrofit tests onto already-moved code |
| SQLite lock contention causing data loss in production | HIGH | Enable WAL immediately (single-line config change, no migration needed); audit `OperationalError` swallowing sites; add `BEGIN IMMEDIATE` to write transactions |
| `utcnow()` replacement causing scheduler UTC regressions | MEDIUM | Revert the mechanical replace; do per-file with downstream tracing; use regression tests modeled on `test_calendar_rrule_until_utc.py` |
| mypy blanket `# type: ignore` accumulation | MEDIUM | Remove global strict mode; switch to per-module overrides; track ignore count as a metric that must only decrease |
| Dependency lockfile not reproducible | MEDIUM | Regenerate using `pip-compile` or `uv lock` on a clean Docker image matching CI; do not re-use a dev environment lockfile |
| Auth regression from route split (cross-owner access) | HIGH | Immediate: add an owner-check regression test that fails to prove the regression; fix by adding `owner` parameter to the extracted function; add to security regression suite |
| Reverted PR orphaned code introducing a security stub | MEDIUM | `git diff <revert-commit>^...<revert-commit>` to enumerate all files changed by the revert; verify each file was fully restored to pre-PR state |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Refactoring without direct coverage (Pitfall 1) | Phase 1: Baseline scorecard — coverage gap fill gate | `pytest --cov` report shows no target file in "missing" column before split PR merges |
| Stub-mismatch after module split (Pitfall 2) | Phase 2: God-file splits — pre-merge checklist | `git grep` for deprecated stub paths returns 0 |
| Import-order singleton breakage (Pitfall 3) | Phase 2: God-file splits — lazy-import audit | `python -c "import <new_package>"` without `app.py` running produces no AttributeError |
| Scope creep into redesign (Pitfall 4) | Phase 2: God-file splits — PR template enforcement | Split PR has no new Pydantic models, no changed call signatures, no new `services/` subdirectories |
| SQLite lock contention silently swallowed (Pitfall 5) | Phase 3: Database hardening — first action | `grep -r 'sqlite3.connect(' src/ routes/ core/ mcp_servers/` returns only helper definition; no `OperationalError` swallowing without logging |
| datetime.utcnow() naive/aware mixing (Pitfall 6) | Phase 4: Deprecated API cleanup — per-file with downstream audit | `grep -rn 'utcnow'` returns 0; `test_calendar_rrule_until_utc.py`-style tests for each replaced scheduler site |
| Auth/owner-scoping regression during route splits (Pitfall 7) | Phase 2: God-file splits — mandatory per-PR | Cross-owner test exists for every data-fetching function extracted from a route module |
| mypy over-aggressive adoption (Pitfall 8) | Phase 1: Baseline tooling setup | `mypy` runs with zero errors against a lenient baseline config; per-module overrides document the ratchet |
| ruff rule churn breaking CI (Pitfall 9) | Phase 1: Baseline tooling setup | ruff version pinned; `pyproject.toml` has explicit rule set without `preview = true` or `select = ["ALL"]` |
| Dependency pinning transitive breakage (Pitfall 10) | Phase 1: Baseline scorecard | Lockfile generated via `pip-compile`/`uv lock` on clean Docker; validated on second clean install |
| Flaky async tests under coverage (Pitfall 11) | Phase 1: Baseline + CI setup | Coverage baseline run on full suite; zero flaky tests with `--cov` before refactoring starts |
| False security confidence from existing tests (Pitfall 12) | Phase 1 (enumerate) + Phase 5 (audit) | Authenticated endpoint enumeration list complete; each has a cross-owner test |
| Secret handling regression from path refactor (Pitfall 13) | Phase 3: Database hardening | Round-trip encrypt/decrypt test in any DB path PR; startup key-accessibility health check |

---

## Sources

- Odysseus `.planning/codebase/CONCERNS.md` — confirmed SQLite WAL/busy_timeout absent, 21 raw connection sites,
  `OperationalError` swallowing, `utcnow()` in 24 files, coverage gaps in god-files, encryption key co-location.
  HIGH confidence (first-party codebase audit).
- Odysseus `.planning/codebase/ARCHITECTURE.md` — confirmed lazy-import convention, module-level singletons,
  setter-based wiring, facade re-export pattern. HIGH confidence.
- Odysseus `.planning/codebase/TESTING.md` — confirmed no coverage gate, stub-by-path pattern, import-inside-function
  pattern for test isolation. HIGH confidence.
- Odysseus `.planning/PROJECT.md` — confirmed behavior-preserving constraint, "fill coverage before refactoring"
  as a key decision, scorecard-driven done criteria. HIGH confidence.
- SQLite concurrency: https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/
  (WAL mode, BEGIN IMMEDIATE, busy_timeout behavior). MEDIUM-HIGH confidence (verified against SQLite documentation).
- SQLite BEGIN IMMEDIATE gotcha: https://berthug.eu/articles/posts/a-brief-post-on-sqlite3-database-locked-despite-timeout/
  (busy_timeout does not protect read-to-write lock upgrades). MEDIUM confidence (single source, consistent with
  SQLite docs).
- mypy incremental adoption: https://eightfold.ai/engineering-blog/static-type-checking-large-scale-python-codebase/
  (Instagram 8-month case study, per-module overrides). MEDIUM confidence.
- datetime.utcnow() deprecation: https://blog.miguelgrinberg.com/post/it-s-time-for-a-change-datetime-utcnow-is-now-deprecated
  and https://docs.python.org/3/library/datetime.html (Python 3.12 deprecation, removal in 3.14). HIGH confidence.
- ruff preview mode instability: https://docs.astral.sh/ruff/linter/ (official ruff docs on preview mode).
  HIGH confidence.
- Dependency lockfile: https://pip.pypa.io/en/stable/topics/repeatable-installs/ (pip repeatable installs).
  HIGH confidence.
- pytest-asyncio event loop scoping issues: https://pytest-asyncio.readthedocs.io/en/stable/reference/changelog.html
  (confirmed class-scoped loop fixture bugs). MEDIUM confidence.
- FastAPI check_same_thread discussion: https://github.com/fastapi/fastapi/discussions/5199
  (SQLAlchemy session threading in async context). MEDIUM confidence.

---

*Pitfalls research for: Odysseus — large Python/FastAPI monolith modernization + security audit*
*Researched: 2026-06-03*
