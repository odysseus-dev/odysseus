# Odysseus Testing Standard & Taxonomy

## Purpose

This document defines *how we write and refactor tests* in Odysseus. It is the
standard that the incremental test-suite refactor (issue #2523) works toward,
and it applies to both human contributors and coding agents.

It is intentionally split from [`tests/README.md`](./README.md):

- **`README.md`** - the concrete, current helper reference: what each helper in
  `tests/helpers/` does and how to call it.
- **`TESTING_STANDARD.md`** (this file) - the rules and taxonomy: what a good
  test looks like, where it belongs, and the policy refactor PRs must follow.

When the two ever disagree, this file states the *intent* and `README.md` states
the *current mechanics*; fix whichever is stale.

This document changes no test behavior. It is guidance only.

## What the test suite is for

The goal is not only to reorganize `tests/`. The goal is for the suite to be a
reliable foundation for future development: deterministic, modular, informative,
behavior-focused, and complete enough to replace manual QA wherever practical.

Run tests with the project virtualenv interpreter (`.venv/bin/python -m pytest`).
The system `python3` may be missing pinned dependencies (e.g. `nh3`), which
shows up as import/collection errors that are environmental, not real failures.

## What "done" means for a single test

Every new or refactored test should be:

- **Deterministic** - same result every run, no reliance on wall-clock, network,
  RNG seeds, or collection order.
- **Behavior-first** - asserts on observable behavior, not on the source text or
  AST of the code under test (see [Behavioral-first policy](#behavioral-first-policy)).
- **Explicit** - setup and expected result are visible in the test, not hidden in
  broad fixtures.
- **Isolated from global process state** - no leaked `sys.modules`, `os.environ`,
  CWD, or package parent-attribute mutation (see [Determinism & isolation](#determinism--isolation-rules)).
- **Order-independent** - passes regardless of which tests ran before it.
- **Environment-independent** - does not assume a venv layout, a developer's home
  directory, an existing `./data` dir, or optional packages that may be absent.
- **Informative on failure** - the assertion message or structure makes the cause
  obvious without a debugger.
- **Small** - understandable quickly; one behavior per test where practical.
- **Backed by shared helpers only when duplication is proven** - not abstracted
  preemptively.

## Test taxonomy

Tests are classified by the categories below. Today the suite is flat under
`tests/`; the **Target dir** column is the phased layout from #2523 that we move
toward *after* helpers and determinism are stable. Until a category is moved,
new tests in that category stay in flat `tests/` but should still follow this
standard.

| Category | What it covers | Examples today | Target dir |
|---|---|---|---|
| **Route / API integration** | Real ASGI request/response, auth gates, admin gates, owner isolation through the app | files using `TestClient` | `tests/routes/` |
| **CLI / script** | `scripts/` entry points and dev tooling | `tests.helpers.cli_loader.load_script` users, `test_pr_blocker_audit.py` | `tests/cli/` |
| **Frontend / JS** | Browser-coupled JS run via Node subprocess; streaming-render invariants | `*_js.py` wrappers, `tests/streaming/*.test.mjs` | `tests/js/` |
| **Tool execution / parsing** | Tool-call parsing, malformed/nonstring args, tool policy | `test_unknown_tool_calls.py`, `test_tool_policy.py`, `*_nonstring.py` | `tests/unit/` or `tests/services/` |
| **LLM / provider** | Provider response parsing, streaming, sanitize, reasoning fallback | `test_llm_core_*`, `test_anthropic_response_parse.py` | `tests/services/` |
| **Session / history / DB** | Session lifecycle, history, schema, ownership at the data layer | `test_session_*`, `test_sqlite_foreign_keys.py` | `tests/services/` or `tests/unit/` |
| **Security / owner-scope / regression** | Owner isolation, auth, SSRF, path confinement, XSS, prompt injection, pinned regressions | `*_owner_scope.py`, `test_security_regressions.py`, `test_*ssrf*`, `test_*confinement*` | `tests/security/` |
| **Cookbook / bootstrap** | Model serve lifecycle, dependency completion | `test_cookbook_*` | `tests/services/` |
| **Scheduler / background** | Cron computation, background jobs, delivery | `test_compute_next_run_*`, `test_bg_*`, `test_task_scheduler_*` | `tests/services/` |
| **Import / module isolation** | The isolation helpers themselves and their guarantees | `test_helpers_import_state.py` | `tests/unit/` |

A test that genuinely spans categories (e.g. a route test that also pins a
security invariant) is classified by its **primary** assertion target and may be
split if it grows.

## Determinism & isolation rules

Do not mutate shared process state without a controlled helper and guaranteed
cleanup. Specifically:

- **`sys.modules` / parent-package attributes** - never assign at module scope.
  Use `tests.helpers.import_state.preserve_import_state`, `clear_module`, or
  `monkeypatch.setitem(sys.modules, ...)`. Restoring `sys.modules` alone is not
  enough; the parent-package attribute must be restored too (the import-state
  helpers handle both).
- **`os.environ`** - use `monkeypatch.setenv` / `monkeypatch.delenv`, never raw
  `os.environ[...] = ...` that outlives the test.
- **Current working directory** - never `chdir` without restoring; never assert
  against cwd-relative paths like `./data`. Use a temp workspace helper instead.
- **Database** - the root `conftest.py` defaults `DATABASE_URL` to an in-memory
  SQLite for collection safety. A test that needs a real file-backed DB must opt
  in explicitly via `tests.helpers.sqlite_db.make_temp_sqlite` and bind its
  `SessionLocal` onto the module under test. Do not rely on a persistent
  on-disk DB existing.
- **Optional dependencies** - do not require packages that may be absent in a
  clean environment (e.g. `python-multipart`). Guard or stub them locally.
- **Node-subprocess JS tests** - skip cleanly when `node` is absent
  (`shutil.which("node")`), matching the existing wrappers. Treat a skip as a
  coverage gap to be aware of, not a pass.
- **Order independence** - a test must not depend on a sibling having imported,
  cached, or stubbed something first. Order-sensitivity is a bug to fix, not a
  constraint to encode.

## Behavioral-first policy

Prefer tests that exercise real behavior over tests that inspect source code.

- **Avoid** `read_text()` + substring assertions, `ast.parse`, and
  `inspect.getsource` checks when the behavior can be driven directly. Source-text
  assertions break on benign refactors (renames, reformatting) and can pass even
  when behavior regresses, because the asserted string still appears somewhere.
- **Prefer** calling the function/route and asserting the outcome. Example: to
  pin owner-scoping of `get_upcoming_events`, seed a temp DB with two owners and
  assert one owner cannot see the other's events - rather than asserting the
  source contains `q.filter(CalendarCal.owner == owner)`.
- **Narrow exception** - a source-text/AST assertion is acceptable only when the
  invariant cannot be practically exercised at runtime (e.g. pinning that a
  required constant or guard literally exists in a module that is hard to drive).
  When used, say *why* in the test docstring so it is a deliberate choice, not a
  shortcut.
- Do not convert source-text assertions to behavioral ones in the *same* PR that
  moves files or changes unrelated setup.

## Helper & factory extraction rules

- Extract a shared helper only when the duplicated shape is **proven** - the same
  setup repeated (ideally byte-identical) across multiple files.
- Prefer **plain functions** in `tests/helpers/` over fixtures. Reach for a
  fixture only when it is clearly scoped to one directory/category, and put it in
  that directory's `conftest.py`, not the root.
- Keep the **root `conftest.py` minimal** - `sys.path`, the DB-URL default, and
  not-installed heavy-dependency stubs only. It is not a place for
  feature-specific fixtures.
- Each helper documents its **intended use and its limits** ("do not stretch this
  to cover X"), as the existing helpers in `README.md` do.
- Do not build a generic abstraction layer (factory framework, broad base
  fixtures) before the repeated semantics are clear. Small and boring beats
  clever and general.
- Candidate factories, to add only after the duplication audit confirms the
  shapes: fake users, fake sessions, fake requests, fake DB rows, fake LLM
  responses, fake tool calls.

## PR discipline for #2523 refactor slices

- Keep each PR small, reviewable, and behavior-preserving - unless the PR's stated
  purpose is to add new coverage.
- **One kind of change per PR.** Do not mix:
  - file moves with assertion changes;
  - helper extraction with logic changes;
  - import-state cleanup with DB-fixture changes.
- Do not weaken assertions, add `skip`/`xfail`, or delete coverage just to make CI
  green. A red test is a signal to investigate, not to silence.
- Prefer 3-6 files per refactor batch, and only when they share the *same*
  pattern.
- Distinguish a stale test expectation from a real production-policy change before
  "fixing" a failing test - never edit a test to match a regression.

## Validation expectations

Run locally before opening or approving a refactor PR:

- `git diff --check` - whitespace and conflict-marker errors.
- `python3 -m py_compile <changed .py files>` - changed files compile.
- Focused `pytest` on the changed files (use `.venv/bin/python -m pytest`).
- `pytest` on neighboring / order-sensitive groups that share import state with
  the changed files.
- When replacing boilerplate, `grep` for the old pattern to confirm no stragglers.
- When changing a helper itself, validate in a fresh worktree so stale
  `__pycache__` or import state cannot mask a regression.
- For order-sensitivity, a randomized run (once `pytest-randomly` is available in
  the dev environment) is the strongest check; record the seed on failures.

## Target directory structure (phased)

Move toward this layout *gradually*, only after helper conventions and
determinism are stable. Low-risk categories move first; oversized catch-all files
are split last.

```
tests/
  conftest.py        # stays minimal
  README.md          # helper reference
  TESTING_STANDARD.md
  helpers/           # plain helper functions (exists)
  unit/              # pure helper/module tests
  cli/               # scripts/ + CLI tests
  js/                # node-subprocess + streaming tests
  security/          # owner-scope, auth, SSRF, confinement, regressions
  routes/            # TestClient integration (per-dir conftest for the client)
  services/          # service-layer tests
  integration/       # only if a cross-cutting flow needs it, later
```

Suggested move order: **js / cli first → security / routes / services → split
oversized catch-all files last.** Each move is mechanical (no assertion changes
in the same PR), with an identical pass set before and after.

## Related: CI-hardening track (tracked separately)

Making the suite an enforced gate is broader than #2523's organization scope and
should be tracked as its own effort. The intended sequence:

1. Add non-blocking randomized pytest reporting (`pytest-randomly`) so hidden
   order-dependence becomes visible without changing any test.
2. Fix surfaced order-dependence in small same-pattern batches.
3. Add coverage reporting with no threshold gate.
4. Only then make the pytest job a blocking CI gate.
5. Consider `pytest-xdist` / parallel isolation after deterministic
   single-process randomized runs are stable.
