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

The shared static-server fixture binds an ephemeral loopback port and publishes
`ODYSSEUS_TEST_STATIC_ORIGIN` to browser tests and their Node subprocesses.
`ODYSSEUS_TEST_STATIC_PORT` can pin a port for an external client; leave it unset
for parallel runs. An occupied explicit port is refused rather than reused. The
server handles each connection on its own thread, so a speculative browser
connection that never sends a request cannot stall the requests behind it.

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

The full suite does not come back clean on every machine. [KNOWN_FAILURES.md](KNOWN_FAILURES.md) lists which failures are expected, which are test bugs worth fixing, and the prerequisites a clean run needs; anything not on that list is a regression until shown otherwise.

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

## Parallel shards (`--shard N/M`)

CI no longer runs the whole suite as one workload. The `python-tests` job is a
four-way matrix, and each job runs one section:

```bash
./venv/bin/python -m pytest -q --shard 1/4
```

`tests/_shards.py` owns the partition and `tests/conftest.py` applies it. The
unit of a shard is a **test file**, so tests that share module state stay
together, and assignment is a total function of the file path - every file
lands in exactly one shard, and the four shards together run every test exactly
once. The partition is deliberately *not* built on the `area_*` markers: those
do not partition the suite, because a file may carry a hand-applied `area_*`
mark on top of the one derived from its filename.

Sharding deselects; it does not narrow collection. Every test module is still
imported, in the same order, in every shard, so the import-time stubbing in
`conftest.py` behaves identically whether the suite runs whole or in sections.
Only the deselected tests' call phase is skipped.

Balance comes from the `slow` marker: a `slow` item is weighted far above an
ordinary one, and files are packed heaviest-first into the lightest shard. The
plan depends only on the collected file set, so every parallel job computes the
same one from the same commit. As more tests earn a `slow` mark from duration
evidence, the sections even out further - no duration table to keep current.

`--shard 1/1` is a no-op, and a selector that is malformed or out of range ends
the run with a usage error rather than quietly testing a subset. If you change
the shard count, change `DEFAULT_SHARD_COUNT` and the `ci.yml` matrix together;
`tests/test_shards.py` fails when they drift apart.

## Local pytest workers

Install the application environment and parallel test tooling with
`python -m pip install -r requirements-dev.txt`. Parallelism is opt-in; ordinary
pytest remains serial, and the four CI shards are unchanged.

```bash
python -m pytest -q -n 4 -p no:cacheprovider --max-worker-restart=0
python -m pytest -q -n 0 -p no:cacheprovider  # full serial release oracle
```

See [the Wave 6 measurements](WAVE6_TEST_PERFORMANCE_REPORT.md) before choosing
a worker count. `-n auto` uses physical cores through xdist's psutil extra; it
still needs enough memory for each worker's collection and application imports.

Before collection, `tests.helpers.worker_runtime` gives each process private
data, attachment, embedding-cache, browser-runtime, and temporary directories.
pytest's basetemp is the controller root's `pytest` directory, with xdist's
`popen-gw<n>` beneath it, which keeps `tmp_path` Unix sockets inside the
107-byte path limit. An explicit `--basetemp` still wins.
An atomic random suffix separates concurrent invocations with the same worker
label; paths inside the root have stable names. Function fixtures still own
their databases and test-specific state. Normal teardown removes the root and
restores the environment. A hard-killed standalone process can leave its owned
root behind, but later runs allocate a fresh namespace and never adopt it.

`APP_PORT` opts into tests against an externally launched smoke application.
Run that suite with `-n 0` so its accounts, endpoints, and application data have
one owner. Unset `APP_PORT` for ordinary unit/regression invocations. Selected
live smoke tests are rejected under xdist with a collection failure; `-m
"not serial"` may exclude them. The existing smoke skips when no application
is launched remain visible in full-suite counts.

Measurements disable pytest's advisory cache to keep concurrent invocations
from sharing last-failed metadata. They also disable worker restart so crashes
remain immediately visible. No test retries or default worker count are added.

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

## CSS computed-style snapshot

`tests/test_css_computed_style_snapshot.py` pins the rendered result of
the shipped ordered stylesheet cascade, whose behavior depends on source order,
by hashing `getComputedStyle` over a fixed element inventory across pages,
viewports, themes and density modes. Any PR that moves CSS has to produce an
identical digest or explain why it did not.

```bash
./venv/bin/python -m pytest tests/test_css_computed_style_snapshot.py
./venv/bin/python scripts/css_snapshot.py --check            # standalone, no pytest
./venv/bin/python scripts/css_snapshot.py --write-baseline   # re-record, deliberately
```

The inventory, the baseline and the capture live in `tests/css_snapshot/`;
`tests/css_snapshot/README.md` documents what is covered, what is deliberately
not, and how to find the property that moved when it fails. The run takes about
21 seconds and skips when `npm ci` has not been run.

## Release smoke suite

`tests/smoke/` drives every advertised feature area once, end to end,
against a real instance - the safety net the unit suite does not provide
for a route move or a module split. One command boots the worktree and
runs it:

```bash
scripts/odysseus-smoke              # boot, run every area, stop again
scripts/odysseus-smoke --keep-up    # leave the instance running
scripts/odysseus-smoke --areas      # the coverage table, without booting
```

It reads its target instance out of the environment (`APP_PORT` through
`internal_api_base()`, plus the dev admin account), so under a plain
`pytest` with nothing booted every scenario skips with the reason and
the full suite stays green. Models are served by a deterministic
loopback stub, never a live endpoint; email uses the repo's existing
`ODYSSEUS_EMAIL_FIXTURE` path.

The report is a per-area table that also prints the areas the suite
deliberately does not cover, so it cannot be read as coverage of
everything it omits. `tests/smoke/README.md` documents what is in each
list and why.

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

### `tests.helpers.stylesheets.app_css`

Use when a test asserts on a CSS rule.

- Returns every app stylesheet concatenated in the order `static/index.html`
  loads them, which is the order the cascade actually has.
- App styles live across an ordered cascade; reading one fragment alone ties
  the test to whichever file a rule sits in today, so it goes red
  when a rule moves without the rendered page changing.
- `stylesheet_paths()` and `stylesheet_urls()` are there when a test needs the
  files or the request URLs rather than their contents.
  `stylesheet_link_tags()` returns the `<link>` markup for a synthetic page
  driven through Playwright, so it gets the whole shipped cascade.
- All of them fail loudly if `index.html` links a stylesheet that is missing.
- Not for vendored CSS under `static/lib/`, which they deliberately skip.

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
