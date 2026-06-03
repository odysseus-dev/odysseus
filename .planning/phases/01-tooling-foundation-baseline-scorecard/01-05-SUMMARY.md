---
phase: 01-tooling-foundation-baseline-scorecard
plan: 05
subsystem: tooling-ci
status: checkpoint
tags: [ci, quality-gate, bandit, security-triage, base-02, dead-code-audit]
requires:
  - "requirements.lock (Plan 01-01)"
  - "[tool.ruff] / [tool.mypy] config (Plan 01-02 / 01-03)"
  - "scripts/scorecard.py + baseline.json (Plan 01-04)"
provides:
  - ".github/workflows/quality-gate.yml — 7-job parallel hard-blocking CI gate"
  - "[tool.bandit] skips — documented per-code security suppressions"
  - ".planning/scorecard/security-triage.md — bandit/pip-audit triage record"
  - "BASE-02-AUDIT.md — recorded revert dead-code audit (SC#4)"
affects:
  - "every future PR (CI gates), once branch protection marks the jobs required (Task 4, human)"
tech-stack:
  added: [bandit==1.9.4, pip-audit==2.10.0, "astral-sh/setup-uv@v8"]
  patterns: ["pyproject [tool.bandit] documented skips", "parallel per-gate CI jobs", "--require-hashes lock installs"]
key-files:
  created:
    - .github/workflows/quality-gate.yml
    - .planning/phases/01-tooling-foundation-baseline-scorecard/BASE-02-AUDIT.md
  modified:
    - pyproject.toml
    - .planning/scorecard/security-triage.md
decisions:
  - "bandit audit: 319 findings, 0 HIGH — all triaged into documented per-code skips, no blanket disable, no code fix needed"
  - "B608 SQL findings verified parameterized/allow-listed false positives (bound ? params; f-string only for computed placeholders or internal table names)"
  - "B104 bind-all are LLM host/URL string defaults, not socket binds — app binds loopback by default"
metrics:
  duration_min: 4
  tasks_completed: 3
  tasks_total: 4
  completed: "2026-06-03"
---

# Phase 01 Plan 05: CI Quality Gate + Security Audit Summary

**STATUS: CHECKPOINT — Tasks 1-3 complete and committed; Task 4 (SC#1 deliberate-violation
demonstration) is a `checkpoint:human-verify` with `gate="blocking-human"` and is PENDING
HUMAN ACTION. The plan is NOT complete.**

CI quality gate wired on the clean baseline: a 7-job parallel hard-blocking
`.github/workflows/quality-gate.yml`, a completed bandit audit pass with documented
`[tool.bandit]` skips (319 findings, 0 high-severity), and a recorded BASE-02 revert
dead-code audit (SC#4 satisfied). The final proof that each gate hard-blocks a real PR
requires a live GitHub PR + branch-protection required checks and is left for the human.

## What Was Built (Tasks 1-3)

### Task 1 — Bandit audit pass + documented `[tool.bandit]` skips (committed `7d0790a`)
- Ran `bandit 1.9.4` (in `python:3.12-slim`, bandit not available on the Py3.14 host) over
  `app.py src routes core mcp_servers companion services` with `-c pyproject.toml`.
- **319 findings: 0 HIGH, 29 MEDIUM, 290 LOW.** No genuine high-severity issue → no code fix
  required. Every finding is either an intentional auth-gated admin feature (THREAT_MODEL.md)
  or a verified false positive.
- Populated `[tool.bandit] skips` with a **per-code rationale comment** (NOT a blanket disable):
  - subprocess/shell admin features: `B404`, `B603`, `B604`, `B607`
  - degrade-gracefully idiom (CLAUDE.md): `B110` (224), `B112` (33)
  - false positives / non-security: `B104` (host strings), `B105` (URLs/numbers), `B108`
    (sandbox tmp), `B311` (display IDs), `B103` (runner chmod), `B608` (parameterized SQL)
- Full per-finding classification recorded in `.planning/scorecard/security-triage.md`.
- **`bandit -c pyproject.toml -r ...` exits 0** against the curated suppression set.

### Task 2 — `.github/workflows/quality-gate.yml` (committed `e2c1523`)
- New workflow `name: ci / quality gate`; triggers `pull_request` + `push: { branches: [main] }`
  (D-03); `permissions: contents: read` (D-02 / T-05-03).
- **Seven independent parallel jobs** (D-02): `lint`, `format`, `mypy`, `pytest`, `bandit`,
  `pip-audit`, `scorecard`. Each starts `actions/checkout@v4` then `astral-sh/setup-uv@v8`
  pinned `version: "0.11.18"` with `enable-cache: true` (D-04).
- Lock-consuming jobs (`mypy`, `pytest`, `scorecard`) install via
  `uv pip install --system --require-hashes -r requirements.lock` (T-05-02, ASVS V10).
- `scorecard` job runs `python scripts/scorecard.py --check` (ratchet, D-11).
- Pinned tool versions; **YAML validated** (`yaml.safe_load`, all 7 jobs present); the two
  doc-check workflows (`issue-description-check.yml`, `pr-description-check.yml`) are unmodified (D-05).

### Task 3 — BASE-02 revert dead-code audit (committed `662c0d0`)
- Re-ran the six SC#4 audit commands as recorded evidence (verification, not removal) and
  wrote `BASE-02-AUDIT.md` with each command + output.
- `git show 67b63e9/1f6c5ac --stat` confirm the reverted file sets.
- codex grep minus `model_routes` → empty (only hits are unrelated OpenAI model-name filter
  strings in `model_routes.py:425-426`); prompt-bar-resize grep → empty.
- `integrations/codex` and `routes/codex_routes.py` absent.
- `ruff check . --select F401,F811` → **0** (`All checks passed!`).
- **Conclusion: both reverts left no dead code; BASE-02 / SC#4 satisfied.**

## Deviations from Plan

None — Tasks 1-3 executed exactly as written. (Bandit and ruff were run inside
`python:3.12-slim` with the locked deps, per the known-environment note that the local Py3.14
host lacks these tools; this is the project's established verification harness, not a deviation.)

## Repo Cleanliness (verified post-edit, in python:3.12-slim)

- `ruff check .` → clean
- `ruff format --check .` → clean
- `ruff check . --select F401,F811` → 0
- `pyproject.toml` parses as valid TOML; `quality-gate.yml` parses as valid YAML

## PENDING HUMAN CHECKPOINT — Task 4 (SC#1 deliberate-violation demonstration)

**Type:** `checkpoint:human-verify`, `gate="blocking-human"`. This CANNOT be auto-approved
or simulated — it requires a live GitHub PR + branch-protection required checks. The executor
deliberately did NOT fabricate or sign off on this demonstration.

**Human verification steps (from the plan `<how-to-verify>`):**
1. Push the Phase-1 branch and open a PR so `ci / quality gate` runs; confirm all seven jobs
   are GREEN on the clean baseline.
2. In branch protection, mark the six gate jobs (`lint`, `format`, `mypy`, `pytest`, `bandit`,
   `pip-audit`) — and optionally `scorecard` — as **REQUIRED** status checks (D-01).
3. Open a throwaway PR injecting ONE deliberate violation per gate and confirm each
   corresponding required check goes RED and blocks merge:
   - `lint`: add an unused import (F401)
   - `format`: introduce a change `ruff format` would undo
   - `mypy`: add a type error (assign a `str` to an `int`-annotated var)
   - `pytest`: add an `assert False` test
   - `bandit`: add a `subprocess.run(..., shell=True)` with untrusted input (a non-triaged HIGH pattern)
   - `pip-audit`: (optional) pin a known-CVE dependency in a scratch lock
4. Confirm merge is blocked while any required check is red. Close the throwaway PR
   (do NOT commit the violations).

**Resume signal:** Type `"approved"` with a note that each required gate went red and blocked
merge, or describe which gate did not block.

## Self-Check: PASSED

Created files verified present:
- `.github/workflows/quality-gate.yml` — FOUND
- `.planning/phases/01-tooling-foundation-baseline-scorecard/BASE-02-AUDIT.md` — FOUND
- `.planning/scorecard/security-triage.md` (modified) — FOUND
- `pyproject.toml` (modified) — FOUND

Task commits verified in git log:
- `7d0790a` (Task 1, bandit) — FOUND
- `e2c1523` (Task 2, workflow) — FOUND
- `662c0d0` (Task 3, BASE-02 audit) — FOUND

Task 4 intentionally NOT executed (blocking-human checkpoint).
