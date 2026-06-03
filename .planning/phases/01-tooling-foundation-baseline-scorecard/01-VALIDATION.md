---
phase: 1
slug: tooling-foundation-baseline-scorecard
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-06-03
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Phase 1 is pure instrumentation (no app behavior change) — the 355-file pytest suite is the
> behavior contract that must stay green after every code-touching step (ruff `--fix`, `ruff format`).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (`asyncio_mode = "auto"`) |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths=["tests"]`) |
| **Quick run command** | `pytest -q` |
| **Full suite command** | `pytest` (355 test files — behavior contract) |
| **Estimated runtime** | ~ (measure during Wave 0; record in scorecard perf metric) |
| **New dep needed** | `pytest-cov` (coverage-map metric; not yet declared — Wave 0) |

---

## Sampling Rate

- **After every code-touching task commit** (ruff `--fix`, `ruff format`, mypy ignores): Run `pytest` (FULL suite — these touch many of the 560 `.py` files; the contract must hold).
- **After every non-code task** (config, lockfile, scorecard, CI): Run the relevant single gate command (`ruff check .` / `ruff format --check .` / `mypy --no-incremental .` / `python scripts/scorecard.py --check`).
- **After every plan wave:** Run `pytest` (full).
- **Before `/gsd-verify-work`:** Full suite green + all six CI jobs green on a real PR + the SC#1 deliberate-violation demonstration + the SC#3 second-container install.
- **Max feedback latency:** full suite runtime (measure Wave 0).

---

## Per-Task Verification Map

> Task IDs are assigned by the planner. This map is requirement-anchored; the planner fills Task ID / Plan / Wave columns to match PLAN.md.

| Task (req anchor) | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|-------------------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| Lockfile compile + 2nd-env install | 1 | TOOL-03 / SC#3 | T-supply-chain | Hash-pinned `--require-hashes` install resolves clean | integration | `docker run --rm -v "$PWD":/w -w /w python:3.12-slim pip install --require-hashes -r requirements.lock` → exit 0 | ❌ W0 (requirements.lock) | ⬜ pending |
| ruff config + zero-finding baseline | 2 | TOOL-01 / SC#5 | — | No `select=["ALL"]`/`preview=true`; pinned ruff | automated | `ruff check .` exits 0; grep pyproject for forbidden keys | ❌ W0 (pyproject [tool.ruff]) | ⬜ pending |
| whole-repo format stable | 2 | TOOL-01 / SC#1 | — | Formatter output is stable | automated | `ruff format --check .` exits 0 | ❌ W0 (.git-blame-ignore-revs) | ⬜ pending |
| mypy lenient zero-error baseline | 3 | TOOL-02 / SC#5 | — | Lenient global + overrides, no global strict | automated | `mypy --no-incremental --config-file pyproject.toml .` exits 0 | ❌ W0 (pyproject [tool.mypy]) | ⬜ pending |
| scorecard generator + baseline.json | 3 | BASE-01 / SC#2 | — | All 7 metrics measured; ratchet catches regression | automated | `python scripts/scorecard.py --write` produces complete baseline.json; `--check` exits nonzero on seeded regression | ❌ W0 (scripts/scorecard.py) | ⬜ pending |
| CI parallel quality gate | 4 | TOOL-04 / SC#1 | T-secure-build | Each of 6 gates hard-blocks; deliberate violation fails the PR | manual (documented) | Throwaway PR injecting one violation per gate; confirm each required check goes red | ❌ W0 (.github/workflows/quality-gate.yml) | ⬜ pending |
| BASE-02 revert audit note | 4 | BASE-02 / SC#4 | — | No dead code from reverts 67b63e9 / 1f6c5ac | automated + manual | audit grep/ls return empty; `ruff check . --select F401,F811` == 0 | n/a (verified clean in research) | ⬜ pending |
| coverage instrumentation stability | 1 | Pitfall 11 | — | Suite stable + deterministic under `--cov` | automated | `pytest --cov=.` runs green and deterministic | ❌ W0 (pytest-cov) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `requirements-dev.in` (+ pinned `requirements-dev.lock`) — pin ruff==0.15.15, mypy==2.1.0, bandit==1.9.4, pip-audit==2.10.0, pytest-cov; establish before CI references them.
- [ ] `pytest-cov` installed + a one-time `pytest --cov=.` stability run (Pitfall 11) BEFORE wiring the coverage metric; add `concurrency = "greenlet,thread"` in `[tool.coverage.run]` if async flakiness appears.
- [ ] `scripts/scorecard.py` — does not exist; build it (the only bespoke code).
- [ ] Confirm a cheap unauthenticated key-path endpoint exists for the perf sample (e.g. health); else fall back to import-time-only timing.
- [ ] Confirm Docker access to pull `python:3.12-slim` (required for lock generation D-09 + SC#3 validation — no fallback).

*No new pytest test files are required by Phase 1 — the existing suite is the contract. Coverage gap-fill is Phase 2 (out of scope here).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Each CI gate hard-blocks a real PR | TOOL-04 / SC#1 | Requires a live GitHub PR + branch-protection required-checks; cannot be asserted from a local unit test | Open a throwaway PR injecting one violation per gate (unused import → lint F401; reformat → format; type error → mypy; `assert False` test → pytest; `subprocess(shell=True)` → bandit). Confirm each required check goes red and blocks merge. Close PR. |
| Lock reproducibility on a second clean env | TOOL-03 / SC#3 | Requires a fresh container distinct from the generation env | Run the `python:3.12-slim` `pip install --require-hashes -r requirements.lock` in a clean container → exit 0. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (requirements-dev pins, pytest-cov, scorecard.py, Docker, perf endpoint)
- [ ] No watch-mode flags
- [ ] Feedback latency < full-suite runtime (measured Wave 0)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
