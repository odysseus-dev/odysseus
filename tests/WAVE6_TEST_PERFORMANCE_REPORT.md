# Wave 6 test performance and worker isolation

Starting branch: `wave6/test-performance-foundation`.
Starting HEAD: `bfa5ebb379713790bd1b47c75237fb1fbe989375`.
Starting tree: `3aa232c1c7d479d7ed48e22aa59ddfe8f53058ad`.

The interrupted working tree was inspected before edits. It contained changes
in `pyproject.toml`, `tests/conftest.py`, `tests/test_research_report_read.py`,
and `tests/test_stt_leak.py`, plus untracked `requirements-dev.txt`,
`tests/helpers/worker_runtime.py`, and `tests/test_worker_runtime.py`.
All seven files belonged to this lane. No working-tree state was discarded.

The interrupted implementation established private filesystem defaults before
application imports, isolated saved research reports and STT temporary-file
observations, declared optional xdist tooling, and guarded externally launched
smoke tests. The temporary-directory context manager, restoration tests,
subprocess inheritance test, report fixture, and STT fixture were coherent.
The root integration and refusal diagnostics needed additional validation and
correction. None of the recovered files was rejected or replaced wholesale.

The final root wiring preserves the existing generated configuration-reference
locations and does not add test fixture environment reads to the public
configuration inventory. The first serial profile exposed two stale-reference
failures caused by the interrupted wiring. Both are fixed within test code;
`website/configuration-reference.md` and its generator remain unchanged.

The live-smoke guard now runs after marker/shard deselection, before xdist
publishes runnable items. It emits a normal collection failure. Raising a
worker UsageError after notification had produced an xdist internal error and
lost the useful refusal message; the diagnostic probe exposed this and the
final probe proves the intended failure is visible. No smoke request executes
in that refused invocation.

## Resource ownership

`tests/helpers/worker_runtime.py` owns an atomically created temporary root for
each pytest process. The worker label is diagnostic, not an allocation key.
The random suffix prevents two independent invocations of `gw0` from adopting
each other's files. Paths below that root are stable: `data`, `mail`,
`fastembed`, `runtime`, `tmp`, and the controller's `pytest` basetemp. This is
deterministic ownership, not a fixed reusable path. Ordinary serial runs use
the same architecture with label `main`; xdist knowledge stays in the helper
and root hooks.

| Resource | Isolation boundary |
| --- | --- |
| DATABASE_URL and collection engine | Existing foundation forces `sqlite:///:memory:` before import in each process; inherited developer URLs are never opened. |
| File-backed SQLite | Existing fixtures own temporary engines/files, restore bindings without replacing ORM classes, dispose engines before deletion. |
| Postgres | Audited tests use mock dialects/DDL engines; this lane does not connect workers to a shared Postgres database. |
| Runtime data, jobs, publications, process/browser registries | Existing constants derive from private ODYSSEUS_DATA_DIR before collection; narrower fixtures still patch their owned roots. |
| Effects/provenance/evidence | In-memory structures are process-local; file-backed resource publications live under private data/process/browser roots or explicit fixture workspaces. |
| Attachments and embedding cache | Dedicated environment overrides point inside the owned root. |
| Search/cache/generated artifacts | Data-derived caches use the owned data root; explicit artifact fixtures use tmp_path. Installed source/browser assets are read together. |
| Temporary files and shell logs | TMPDIR/TMP/TEMP and cached tempfile.tempdir point into the owned root and are inherited by subprocesses. pytest's basetemp is the controller root's `pytest` directory, with distinct xdist `popen-gw<n>` worker basetemps beneath it. |
| Browser profiles and Unix sockets | Private runtime/data roots; existing short AF_UNIX socket helper owns atomic /tmp directories rather than long pytest paths. |
| Static HTTP servers | Each process holds its kernel-assigned loopback socket; fixed external pins are rejected for parallel execution. Connections get their own threads, so a silent client cannot stall others. |
| Chroma refusal probes | Bound non-listening sockets reserve closed ports until teardown, replacing bind-and-release guesses. |
| Launcher refusal probe | An owned ephemeral listener exercises foreign-server refusal without a host-dependent derived-port skip. Port derivation retains its separate assertions. |
| Process tests | Sleeper fixture owns process sessions/groups, signals the group before reaping its leader, and propagates cleanup errors; it previously killed only the shell and swallowed errors. |
| Module globals/environment | Process-local under xdist; foundation import-state/database restoration guards and test monkeypatches remain intact. |
| Pytest advisory cache | Measurements disable cacheprovider. Built-in worker last-failed/node-ID writers are suppressed by pytest; concurrent controllers should not share advisory metadata. No suite test uses the cache fixture. |
| Live smoke application | Explicit APP_PORT opts into external application ownership; selected live smoke tests require -n 0. Existing no-application skips remain visible. |

Normal cleanup restores the caller's environment/tempfile cache and removes
only the root owned by that context. Cleanup exceptions are not suppressed.
A hard-killed standalone process can leave its root behind; subsequent runs
allocate a different root, so abandoned state is not reused. This is isolation
against crash residue, not a claim that arbitrary SIGKILL removes every file
or process. No global sweeper, kill-by-name, PID-derived namespace, or guessed
per-worker port was added.

The audit found actual filesystem/port/process fixture boundaries to repair,
not a need to serialize ordinary unit tests. Process-local globals alone are
not cross-worker shared state. A delegated broad audit was partial and offered
speculative concerns; those were checked locally rather than accepted as
proven defects.

## Dependencies and release modes

Foundation requirements declared pytest and pytest-asyncio, but not xdist.
The recovered development declaration is retained: `requirements-dev.txt`
includes `requirements.txt` and `pytest-xdist[psutil]>=3.8,<4`. Runtime dependency
requirements remain unchanged. Installed tooling: pytest 9.1.1, xdist 3.8.0,
psutil 7.2.2, Python 3.11. Physical cores: 8; logical CPUs: 16. The psutil extra
makes auto choose physical cores on this host.

Parallelism stays opt-in. The full serial release oracle and four deterministic
CI shards remain unchanged. The two strict negative-web production xfails
remain xfails: memory-only turns are still incorrectly short-circuited with
"Web access is disabled." No production/runtime file, Wave 4 behavior, CI
workflow, or production contract was repaired in this lane.

## Takeover after the interrupted session

Implementation commits `a94fc54c` and `915e6ed1` were inherited at takeover
HEAD `915e6ed12571b0c9036a3546c334c3561443a310`, tree
`6a646603ace8e40e1497212c85ef47b57443f043`. The uncommitted README section and
this report were retained and completed. The inherited commits were audited
against their diffs and kept unchanged.

Two full-suite `-n 2` runs on that HEAD each failed one test. Neither failure
was a product defect, and both are fixed in test infrastructure:

1. `test_legacy_cleanup_against_a_private_real_tmux_server` failed
   deterministically under any worker count with `File name too long`. This
   lane had moved pytest's default basetemp beneath the private `TMPDIR`,
   which gave
   `/tmp/ody-main-XXXXXXXX/tmp/pytest-of-<user>/pytest-0/popen-gw0/<test>/tmux.sock`,
   110 bytes against Linux's 107-byte AF_UNIX limit. The same path was 87 bytes
   before this lane and 100 bytes in a serial run, which is why the serial
   profile passed. The controller now sets basetemp to the private root's
   `pytest` directory, and xdist hands each worker `popen-gw<n>` beneath it.
   That path is now 81 bytes regardless of the username length. An explicit
   `--basetemp` is still honoured. `test_worker_runtime.py` binds an AF_UNIX
   socket at the same path budget without needing tmux, and fails under
   `-n 2` without the fix.
2. With the basetemp fix in place, the next `-n 2` run had a single different
   failure. `test_reordering_two_conflicting_declarations_moves_the_digest`
   ran 31.7 s instead of about 5.3 s, and node crashed with
   `route.fetch: Request context disposed`. That message hides the real
   failure, a 30 s `page.goto` stall: the capture's `finally` closed the
   browser while a stylesheet `route.fetch` was still pending. The shared
   static server was a single-threaded `socketserver.TCPServer`. Chromium
   sometimes opens a speculative connection that never sends a request, and
   every queued request then waited behind it. A server-instrumented capture
   loop under full CPU saturation reproduced it: 4 of 12 captures had a
   request-less connection holding the server for about 28.9 s, and those
   captures failed. With a threaded server, 12 of 12 captures passed. The idle
   connection still appeared in 5 of them but lived 1.0 to 1.5 s without
   blocking anything. The fixture now uses `ThreadingTCPServer` with daemon
   handler threads. `test_static_server_serves_this_worktree` holds a silent
   connection open while it fetches; against the serial server, that request
   times out after 5 s. This defect predates the lane and depends on load,
   which parallel execution increases. Assertions, timeouts, and the capture
   harness are unchanged.

Both fixes keep the existing first-location lines that
`website/configuration-reference.md` records, and that page is unchanged. The
abandoned root of the first `-n 2` run, killed by its monitor, was
`/tmp/ody-main-x7hmcf4_` (261 MB, last written before takeover). No process
held it, and it was removed. No later run left a root behind.

## Measurements

Host: Python 3.11.15, pytest 9.1.1, pytest-xdist 3.8.0, psutil 7.2.2, 8 physical
cores, 16 logical CPUs, 29 GiB RAM. Every run collects the same full suite. The
command is `python -m pytest -q -p no:cacheprovider --durations=100 -rsx
--max-worker-restart=0 -o faulthandler_timeout=60 -n <N>`. The serial
performance-lane profile predates the last option pair. The measurement wrapper
records wall time and descendant RSS. After each run it checks for surviving
descendants (pid plus create time), new TCP listeners, mutated files under
`data/` and the inherited data directory, and leftover `/tmp/ody-main-*`
runtime roots.

| Run | Code | Workers | Wall s | Passed | Failed | Skipped | XFail | Subtests | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Isolation foundation oracle | `bfa5ebb3` | 0 | — | 12,556 | 0 | 59 | 2 | 6 | — |
| Lane serial profile | pre-`a94fc54c` wiring | 0 | 506.4 | 12,558 | 2* | 59 | 2 | 6 | — |
| full-n2 | `915e6ed1` | 2 | n/a† | — | 1 (`F` marker) | — | — | — | — |
| full-n2-measured | `915e6ed1` | 2 | 262.1 | 12,559 | 1 (tmux) | 59 | 2 | 6 | 5.5 GB |
| full-n2-r2 | + basetemp fix | 2 | 271.1 | 12,560 | 1 (CSS stall) | 59 | 2 | 6 | 6.3 GB |
| full-n2-r3 | final | 2 | 267.6 | 12,561 | 0 | 59 | 2 | 6 | 5.7 GB |
| full-n4-r1 | final | 4 | 155.7 | 12,561 | 0 | 59 | 2 | 6 | 9.1 GB |
| full-n4-r2 | final | 4 | 155.5 | 12,561 | 0 | 59 | 2 | 6 | 8.7 GB |
| serial-final | final | 0 | 496.3 | 12,561 | 0 | 59 | 2 | 6 | 3.9 GB |

\* Stale configuration-reference failures from the interrupted wiring, fixed
before `a94fc54c`. † The monitor crashed on a hardened Chromium process
(`AccessDenied`) after pytest reached 100%, so no summary was retained. Its
single progress-line `F` matches the deterministic tmux failure.

Every run with the final code had no worker crashes, no hangs, no surviving
test descendants, no new listeners, no persistent-state mutations, and no
remaining runtime roots. Passed counts increase only with the lane's added
tests. The skips are the same 59: 16 smoke tests without `APP_PORT` plus
opt-in live and platform gates. The two xfails are the known strict
negative-web production defects.

Monitoring gaps: the wrapper could not read `/proc/<pid>/environ` for two to
four short-lived `python` descendants per run (`AccessDenied`). That only limits
worker-ID observation. Leak detection uses process identity, not the
environment, and reported nothing. Hardened Chromium processes are tolerated
as unreadable instead of aborting the measurement. No unrelated process was
signalled.

### Scaling and recommendation

Compared with the 496.3 s final serial oracle, two workers are 1.85x faster
(267.6 s) and four workers are 3.19x faster (155.6 s mean). Going from two to
four workers is 1.72x faster. Each worker collects 12,622 items in about 11 s
(0.9 GB RSS) before running anything. The longest single test is the
computed-style capture at about 23 s. The auth-concurrency, rich-document
browser, media, and generated-reference groups keep their real work.

Recommended local configuration: `-n 4`. Both full repeats were green and
within 0.3 s of each other, at about 2.2 GB RSS per worker.

`-n auto` (8 workers here) was not run. Projected from the measured per-worker
peak, it needs about 18 GB, against about 21 GB available on this desktop host
while its browser and Chroma services run. Fixed collection cost and the 23 s
longest test bound the best case to roughly 85 to 100 s. The CSS stall above
also showed that browser timing is sensitive to load. That gain does not
justify the risk of swapping or OOM, or of timing distortion. Evaluate six
workers on a host with more headroom before considering `-n auto`.

The full serial run remains the release oracle and the CI shards are
unchanged. Live smoke tests under `APP_PORT` are the only serial-only tests.
They share one external application, its accounts, and its endpoints, and
are refused under xdist.

No new product defect was found. A follow-up for test tooling: when a
`route.fetch` handler is still pending, `tests/css_snapshot/capture.mjs` can
replace a navigation error with `Request context disposed`. Fixing that would
make future capture failures easier to diagnose; it is not needed for
correctness here.
