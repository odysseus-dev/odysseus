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
python3 -m pytest -m area_security
python3 -m pytest -m "area_services and sub_cookbook"
```

Areas are `security`, `routes`, `services`, `cli`, `js`, `helpers`, `unit`, and
`uncategorized`. Classification is conservative and token-based: a file that
matches no area keyword falls back to `area_uncategorized` with its filename as
the sub-area. The `area_*` names are registered in `pyproject.toml`; the dynamic
`sub_*` names are registered before collection by `pytest_configure` in
`tests/conftest.py`, so unknown-mark warnings still flag genuine typos.

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

## What not to abstract yet

Some remaining patterns should stay as-is for now rather than being forced into
helpers:

- Large mixed files such as security/review regression files.
- Setup-oriented `sys.modules` stub installers.
- One-off custom module patching.
- DB/session/route setup, until it has been audited separately.

## Validation expectations

Run validation locally before opening or approving a PR. Practical checks:

- `git diff --check` - catch whitespace and conflict-marker errors.
- `python3 -m py_compile <changed files>` - confirm changed files compile.
- Focused `pytest` on the changed test files.
- `pytest` on neighboring or order-sensitive test groups that share import
  state with the changed files.
- `grep` for the old boilerplate when replacing it, to confirm no stragglers
  remain.
- A fresh audit worktree when changing the helpers themselves, so stale
  `__pycache__` or import state cannot mask a regression.

## Current roadmap

1. Import-state cleanup - complete.
2. Document helper conventions (this file).
3. Audit fake DB / `SessionLocal` / route setup duplication.
4. Add tiny helpers only when the repeated semantics are clear.
5. Start low-risk file moves only after helper conventions are documented.
6. Avoid moving high-risk security/route regression files first.
