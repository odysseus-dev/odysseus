# Test Suite Notes

## Purpose

This file documents the shared test helpers and the review expectations that go
with them. The suite is being refactored incrementally, so this is a working
reference for that effort - not a claim that the suite is already fully
organized. Read it before adding a new helper or before reviewing a PR that
touches `tests/helpers/`.

For the broader rules - test taxonomy, determinism/isolation rules, the
behavioral-vs-source-text policy, and helper/factory extraction rules - see
[`TESTING_STANDARD.md`](./TESTING_STANDARD.md). This file is the concrete helper
reference; that file is the standard the refactor works toward.

## Running focused subsets (taxonomy markers)

`tests/conftest.py` tags every test at collection time with two markers derived
from its filename by `tests/_taxonomy.py`: an `area_*` marker (e.g.
`area_security`) and a finer `sub_*` marker (e.g. `sub_owner_scope`). This adds
markers only - it moves no files and changes no test behavior. Use them to run a
focused slice:

```bash
./venv/bin/python -m pytest -m area_security
./venv/bin/python -m pytest -m "area_services and sub_cookbook"
```

Areas are `security`, `routes`, `services`, `cli`, `js`, `helpers`, `unit`, and
`uncategorized`. Classification is conservative and token-based: a file that
matches no area keyword falls back to `area_uncategorized` with its filename as
the sub-area. The `area_*` names are registered in `pyproject.toml`; the dynamic
`sub_*` names are registered before collection by `pytest_configure` in
`tests/conftest.py`, so unknown-mark warnings still flag genuine typos.

For common focused runs, use `tests/run_focus.py`. It validates area and
sub-area names, accepts sub-areas with or without the `sub_` prefix, and passes
extra pytest arguments after `--`:

```bash
./venv/bin/python tests/run_focus.py --area security
./venv/bin/python tests/run_focus.py --area services --sub-area cookbook
./venv/bin/python tests/run_focus.py --sub-area sub_cookbook
./venv/bin/python tests/run_focus.py --keyword taxonomy
./venv/bin/python tests/run_focus.py --last-failed
./venv/bin/python tests/run_focus.py --dry-run --area services --sub-area cookbook
./venv/bin/python tests/run_focus.py --area services -- --maxfail=1 -q
```

### Fast lane and duration visibility

`--fast` runs the fast lane: the tests that are *not* marked `slow` (it adds the
marker expression `not slow`). It composes with `--area`/`--sub-area` using
`and`. Because no tests may be marked `slow` yet, `--fast` can initially match
the full focused selection; it becomes a real speed-up as `slow` marks are added
from duration evidence. Use it for quick local or reviewer feedback; it does not
replace broader focused or full-suite validation before merge.

`--durations N` and `--durations-min FLOAT` add pytest's slowest-test reporting
so you can see where time goes. They are reporting only and do not count as a
focus selector, so `--durations` must be combined with a real selector
(`--area`, `--sub-area`, `--keyword`, `--last-failed`, or `--fast`).

Use the project Python environment before running these commands. The examples
use the repo's documented `./venv/bin/python` path so they do not accidentally
fall back to system Python.

```bash
./venv/bin/python tests/run_focus.py --fast
./venv/bin/python tests/run_focus.py --area services --fast
./venv/bin/python tests/run_focus.py --area services --durations 25
./venv/bin/python tests/run_focus.py --area services --fast --durations 25 --durations-min 0.05
```

The `slow` marker is opt-in. Mark a test `slow` only with duration evidence
(from `--durations`), not by guessing - see the fast-lane policy in
`TESTING_STANDARD.md`. `--fast` is for quick reviewer feedback and must not
replace the full suite before merge. A `slow` mark only excludes a test from the
fast lane; the test stays runnable directly, e.g.:

```bash
./venv/bin/python -m pytest tests/test_auth_config_lock_concurrency.py
./venv/bin/python -m pytest -m slow
```

## Order-sensitivity reporting (report-only)

`tests/run_order_report.py` runs pytest with the collected test items shuffled
by a seeded RNG, to surface order-sensitive tests (hidden coupling through
shared import state, module caches, databases, etc.). It is report-only: it is
not wired into CI, adds no gate, and changes no normal pytest collection or
ordering - the shuffle exists only inside this runner. The seed is always
printed, and pytest targets/options go after a literal `--`:

```bash
./venv/bin/python tests/run_order_report.py --seed 123 -- tests/cli/ -q
./venv/bin/python tests/run_order_report.py -- tests/cli/ -q   # generates and prints a seed
```

The same seed reproduces the same order when the reported working directory,
pytest target arguments, and test environment are also the same. The runner
prints all command arguments with shell-safe POSIX quoting and uses the
invoking Python interpreter.

A generated-seed run starts with output like:

```text
[order-report] working directory: /path/to/odysseus
[order-report] shuffling test order with seed 284734921
[order-report] reproduce from this working directory with the same test environment:
[order-report] reproduce with: /path/to/odysseus/venv/bin/python /path/to/odysseus/tests/run_order_report.py --seed 284734921 -- tests/cli/ -q
```

Run the printed command from the reported working directory to reproduce the
same fixed-seed order:

```text
[order-report] working directory: /path/to/odysseus
[order-report] shuffling test order with seed 284734921
[order-report] reproduce from this working directory with the same test environment:
[order-report] reproduce with: /path/to/odysseus/venv/bin/python /path/to/odysseus/tests/run_order_report.py --seed 284734921 -- tests/cli/ -q
```

Pytest output remains visible between the report header and footer. A failing
run ends with pytest's normal failure report followed by:

```text
FAILED tests/example_test.py::test_example - AssertionError
[order-report] seed 284734921: pytest exit code 1 (report-only; fix order-sensitive failures in separate scoped PRs)
```

Failures discovered this way are real isolation bugs: fix them in separate
scoped PRs - do not silence them with `skip`/`xfail`, and do not "fix" them by
depending on a particular order.

The runner propagates pytest's exit code, so it composes with normal local
workflows; "report-only" means it is not a CI gate, not that failures are
swallowed.

## Core principles

- Keep PRs small and homogeneous: one kind of change per PR.
- Prefer explicit local setup over hidden global fixtures.
- Avoid expanding the root `conftest.py` unless absolutely necessary.
- Do not mix file moves with logic changes in the same PR.
- Do not weaken tests with `skip`/`xfail` just to make CI pass.
- Validate the focused files you changed, plus any neighboring or
  order-sensitive groups they interact with.

## Helper conventions

The helpers below live under `tests/helpers/`. They exist to remove repeated
boilerplate that already appeared across multiple tests. Reach for one only when
your test matches its intended use; do not stretch a helper to cover a new case.

### `tests.helpers.cli_loader.load_script`

Use when a test needs to import a script under `scripts/` without repeating
`SourceFileLoader` / `importlib.util` boilerplate.

- Intended for script/CLI tests that load a single file from `scripts/`.
- Not for arbitrary package imports - use a normal `import` for those.
- When migrating an existing test to it, keep the existing stubs and assertions
  unchanged. Any `sys.modules` stubs the script needs at import time must still
  be injected (e.g. via `monkeypatch`) before calling `load_script`.

### `tests.helpers.import_state.clear_module`

Use when a test must drop one cached module and its parent-package attribute
before a fresh import.

- Clears `sys.modules[name]`.
- Clears the parent-package attribute when present.
- Good replacement for local `sys.modules.pop(...)` + `delattr(parent, child)`
  blocks.

### `tests.helpers.import_state.preserve_import_state`

Use when a test temporarily installs stubs into `sys.modules` and needs
deterministic cleanup afterward.

- Context manager: restores both `sys.modules` entries and parent-package
  attributes on exit (normal or exception).
- Useful around module-level stubs or temporary imports.
- Prefer narrow, explicit module names over broad ones.

### `tests.helpers.import_state.clear_fake_database_modules`

Use only for the guarded fake/stub database cleanup pattern.

- Preserves a real-looking `core.database` (one with a string `__file__`).
- Removes a fake/stub `core.database` and the related `src.database` state.
- Do not use as a general database reset fixture.

### `tests.helpers.import_state.clear_fake_endpoint_resolver_modules`

Use only for the guarded fake/stub `src.endpoint_resolver` cleanup pattern.

- Preserves real resolver modules (those with a truthy `__file__`).
- Evicts fake/stub resolver modules and the dependent route modules that were
  cached against them.
- Accepts explicit extra dependent module names to evict alongside the defaults.

### `tests.helpers.sqlite_db.make_temp_sqlite`

Use for the repeated file-backed temp sqlite setup in tests.

- Only constructs `(SessionLocal, engine, tmpfile)` from the repeated block.
- Does not patch modules and does not clean up the temp file.
- The caller must bind `SessionLocal` explicitly onto whatever module the code
  under test reads, and must keep the returned objects alive.
- Do not use it as a general DB fixture framework.

### `tests.helpers.db_stubs.make_core_db_stub`

Use for small import-time `core.database` stubs with a placeholder
`SessionLocal`.

- Pass model names via `models` when MagicMock attributes are sufficient.
- Pass `attributes` when an import needs exact placeholder values.
- Set `install_core_package=True` only when the test also needs a fake parent
  `core` module stub.
- Keep custom fake sessions and route-specific database behavior local.

## What not to abstract yet

Some remaining patterns should stay as-is for now rather than being forced into
helpers:

- Large mixed files such as security/review regression files.
- Broad setup-oriented `sys.modules` stub installers.
- One-off custom module patching.
- Custom DB session, route, and app setup.

## Validation expectations

Run validation locally before opening or approving a PR. Practical checks:

- `git diff --check` - catch whitespace and conflict-marker errors.
- `./venv/bin/python -m py_compile <changed files>` - confirm changed files compile.
- Focused `./venv/bin/python -m pytest` on the changed test files.
- `./venv/bin/python -m pytest` on neighboring or order-sensitive test groups
  that share import state with the changed files.
- `grep` for the old boilerplate when replacing it, to confirm no stragglers
  remain.
- A fresh audit worktree when changing the helpers themselves, so stale
  `__pycache__` or import state cannot mask a regression.

## Current roadmap

1. Import-state cleanup - complete.
2. Document helper conventions (this file).
3. Pilot the repeated import-time `core.database` stub helper.
4. Add further tiny helpers only when the repeated semantics are clear.
5. Start low-risk file moves only after helper conventions are documented.
6. Avoid moving high-risk security/route regression files first.
