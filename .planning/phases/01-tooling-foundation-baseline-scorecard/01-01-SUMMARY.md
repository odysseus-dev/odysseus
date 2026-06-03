---
phase: 01-tooling-foundation-baseline-scorecard
plan: 01
subsystem: dependency-management
tags: [lockfile, supply-chain, uv, tooling, ci-prereq]
requires: []
provides:
  - requirements.in
  - requirements-optional.in
  - requirements-dev.in
  - requirements.lock
  - requirements-optional.lock
  - requirements-dev.lock
affects:
  - Plan 03 (mypy job installs from requirements.lock)
  - Plan 04 (pip-audit metric over requirements.lock; coverage metric)
  - Plan 05 (CI install-from-lock jobs, --require-hashes)
tech-stack:
  added:
    - "uv 0.11.18 (compile-time only; not a runtime dep)"
  patterns:
    - "Two-tier deps: .in intent files compiled to --generate-hashes .lock files inside python:3.12-slim"
    - "Dev/CI tools isolated in requirements-dev.lock, kept out of the runtime requirements.lock"
key-files:
  created:
    - requirements.in
    - requirements-optional.in
    - requirements-dev.in
    - requirements.lock
    - requirements-optional.lock
    - requirements-dev.lock
  modified: []
decisions:
  - "Unified core lock succeeded — D-06 ML-fallback (requirements-ml.in/.lock split) NOT triggered; chromadb-client+fastembed+onnxruntime+numpy resolved together cleanly"
  - "No [tool.coverage.run] concurrency setting needed — coverage suite ran deterministically with no async-coverage flakiness (Pitfall 11 cleared for Plan 04)"
metrics:
  duration_minutes: 5
  completed: 2026-06-03
  tasks: 3
  files: 7
requirements: [TOOL-03]
---

# Phase 01 Plan 01: Dependency-Locking Foundation Summary

Hash-pinned, reproducible dependency lockfiles compiled with uv 0.11.18 inside `python:3.12-slim`, validated to install clean on a second container (SC#3) with the existing pytest suite confirmed stable under `--cov`.

## What Was Built

Three `.in` dependency-intent files and their three `uv pip compile --generate-hashes` lockfiles, establishing the supply-chain root of trust consumed by every later Phase-1 CI gate.

- **requirements.in** — 25 direct core runtime deps copied verbatim (with why-comments) from `requirements.txt`, with `pytest`/`pytest-asyncio` removed (moved to dev).
- **requirements-optional.in** — `faster-whisper`, `duckduckgo-search`, AGPL `PyMuPDF`, `markitdown[...]==0.1.5`, preserving the load-bearing AGPL-3.0 quarantine note.
- **requirements-dev.in** — pinned `ruff==0.15.15`, `mypy==2.1.0`, `bandit==1.9.4`, `pip-audit==2.10.0` plus `pytest`/`pytest-asyncio`/`pytest-cov`.
- **requirements.lock** — 97 packages, 2029 hashes. Unified core resolve.
- **requirements-optional.lock** — 56 packages, 805 hashes.
- **requirements-dev.lock** — 44 packages, 672 hashes. Dev tools confirmed absent from the core lock (no leak).

## Key Outcomes

- **Unified vs ML-split:** Unified. The D-06 ML-fallback branch did **not** trigger — `chromadb-client`, `fastembed`, `onnxruntime`, and `numpy` resolved together in `requirements.lock` with no `ResolutionImpossible`. No `requirements-ml.in`/`.lock` was created.
- **SC#3 (reproducibility):** `docker run ... python:3.12-slim pip install --require-hashes -r requirements.lock` exits 0 on a fresh container distinct from the generation step → **SC3_PASS**.
- **Pitfall 11 (coverage stability):** `pytest --cov=.` ran deterministically inside the container. Full-suite test runtime ≈ **21.5s** (cov instrumented; ≈9s without cov). No async-coverage flakiness or crash. **No `[tool.coverage.run] concurrency = ["greenlet","thread"]` setting is required** for Plan 04 — record this so Plan 04 can leave that line commented.
- **Generation environment:** all locks generated inside `python:3.12-slim` (uv 0.11.18), never the local Python 3.14 host (D-09 drift avoidance). Single-platform lock; `--universal` deliberately not used.
- **Slopsquat review (T-01-SC):** reviewed the full resolved top-level name set in the core lock — all transitive packages are recognizable, mainstream deps (the niquests HTTP stack `niquests/jh2/qh3/wassima` pulled by chromadb-client; the caldav calendar stack `icalendar-searcher/recurring-ical-events/x-wr-timezone`). No slopsquat/unknown-package indicators.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] SELinux volume relabel required for the Docker compile mount**
- **Found during:** Task 2 (first compile attempt)
- **Issue:** The host runs podman (Docker CLI shim) with SELinux **Enforcing**. The plan's recipe `-v "$PWD":/w` produced `error: failed to open file '/w/pyproject.toml': Permission denied (os error 13)` — the container could not read the bind-mounted repo without an SELinux relabel.
- **Fix:** Added the `:z` shared SELinux-relabel flag to every container mount (`-v "$PWD":/w:z`). This is an environment/config fix to the documented recipe, not a dependency or package-install change.
- **Files modified:** none (command-only adjustment).
- **Applies to:** Tasks 2 and 3 container invocations; relevant for any future local re-compile on an SELinux-enforcing podman host.

## Coverage Suite: Pre-existing Failures (Out of Scope)

Running the full suite under `--cov` surfaced **20 failures (1736 passed, 83 skipped)**. These are **pre-existing on the branch and NOT caused by the lockfile**: the identical `20 failed / 1736 passed / 83 skipped` result occurs against the unpinned `requirements.txt` baseline run in the same container. The lock introduces **zero new failures** — it is behavior-equivalent to the prior unpinned install.

The failures are environment/isolation artifacts of a clean container (e.g. `FileNotFoundError: ./venv/bin/python`, missing git working state, cross-process RAG fixtures); several pass in isolation but fail in the full run (ordering/state leakage). Logged to `deferred-items.md`; they belong to the later coverage-gap / test-hardening work, not this dependency-locking plan.

## For Later Plans

- **Plan 03/04/05:** install from `requirements.lock` with `--require-hashes` (proven to work). Dev tools come from `requirements-dev.lock`.
- **Plan 04:** leave `[tool.coverage.run] concurrency` **commented out** — async coverage is stable.
- **Plan 04 perf metadata:** full pytest suite ≈ 9s clean / ≈ 21.5s under `--cov` on `python:3.12-slim`.

## Self-Check: PASSED

- requirements.in — FOUND
- requirements-optional.in — FOUND
- requirements-dev.in — FOUND
- requirements.lock — FOUND (`--hash=sha256:` present)
- requirements-optional.lock — FOUND (`--hash=sha256:` present)
- requirements-dev.lock — FOUND (`--hash=sha256:` present)
- Commit b08fb9d (intent files) — FOUND
- Commit 81793b0 (lockfiles) — FOUND
