# Phase 1: Tooling Foundation & Baseline Scorecard - Research

**Researched:** 2026-06-03
**Domain:** Python tooling adoption on a large brownfield FastAPI monolith — dependency locking (uv), lint/format (ruff), type checking (mypy), CI gates (GitHub Actions), security scans (bandit, pip-audit), and a re-runnable measurement scorecard
**Confidence:** HIGH (tool versions verified against PyPI/registries this session; config shapes verified against installed tools and official docs; all decisions are LOCKED so research is implementation-HOW, not selection)

## Summary

This phase is pure instrumentation: no application code behavior changes. The work is to (1) produce a hash-pinned dependency lockfile generated inside `python:3.12-slim`, (2) bring the repo to a genuinely zero-finding ruff + mypy baseline, (3) wire a parallel-job GitHub Actions quality gate that hard-blocks on a clean baseline, (4) build a re-runnable JSON-first scorecard with a no-regression ratchet, and (5) confirm the two reverts on `main` left no orphaned code (verified during research — they did not).

The dominant risk is **ordering**: `ruff --fix` and `ruff format` will touch many of the 560 Python files, and the 355-file pytest suite is the behavior contract that must stay green at each commit. The locked atomic-commit sequence (config → `--fix` → format) plus `.git-blame-ignore-revs` is the correct mitigation. The second risk is **lockfile resolution drift** — the chromadb-client + fastembed + onnxruntime + markitdown(magika→onnxruntime) group is the transitive-conflict hotspot (Pitfall 10); compiling inside the exact CI base image and validating on a second clean container is the locked, correct guard.

**Primary recommendation:** Sequence as seven independently-verifiable steps — (1) lockfile, (2) ruff config commit, (3) `ruff --fix` commit, (4) `ruff format` commit + blame-ignore, (5) mypy lenient config commit, (6) scorecard generator + baseline.json, (7) CI workflow that consumes all of the above — running the full pytest suite as the green-gate after every code-touching step.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**CI Gating Posture**
- **D-01:** Hard-block on a clean baseline from day 1. Reach zero findings first (ruff auto-fix + targeted per-file ignores; mypy lenient global + overrides), then every gate hard-blocks merges. A deliberate violation must fail the PR check (ROADMAP SC#1). No warn-only ramp.
- **D-02:** Parallel GitHub Actions jobs — separate jobs for lint, format-check, mypy, pytest, bandit, pip-audit. Fast feedback; clear which gate failed.
- **D-03:** Triggers: `pull_request` + `push` to `main`. Keeps main's status truthful and catches direct pushes.
- **D-04:** CI installs via `uv` from the committed lockfile (`setup-uv` action).
- **D-05:** Build on the existing `.github/workflows/` (currently only `issue-description-check.yml` / `pr-description-check.yml`) — add a new quality-gate workflow alongside them; do not modify the doc-check workflows.

**Lockfile Strategy**
- **D-06:** Single unified core lockfile. The chromadb+fastembed+onnxruntime group is resolved inside this unified lock. Fallback: only split into a separate `requirements-ml` group if unified resolution fails to converge in `python:3.12-slim`.
- **D-07:** Compile from a new top-level `requirements.in` (direct deps only) → fully-pinned `requirements.lock`. Do not just freeze the existing loose `requirements.txt`.
- **D-08:** Separate optional lock for `requirements-optional.txt` (AGPL PyMuPDF quarantine). CI installs the core lock by default.
- **D-09:** `uv pip compile --generate-hashes`, generated inside Docker `python:3.12-slim` so the resolve matches CI exactly. Success criterion: `pip install -r requirements.lock` succeeds on a second clean Linux env without `ResolutionImpossible` (ROADMAP SC#3).

**Scorecard**
- **D-10:** JSON is the source of truth; a markdown view is rendered from it.
- **D-11:** Relative no-regression ratchet. Each metric's threshold = its baseline value; CI fails on regression. Improvement-only. A small number of absolute targets the roadmap implies (e.g. zero ruff findings at baseline) are fine, but the default is ratchet.
- **D-12:** Scripted + re-runnable generator (e.g. `scripts/scorecard.py`) regenerates every metric on demand and in CI.
- **D-13:** Location: `.planning/scorecard/` (`baseline.json` + rendered `SCORECARD.md`).
- **D-14:** Metrics (from ROADMAP SC#2): per-module line counts, mypy-typed %, ruff finding count, security findings list, per-file test-coverage map, startup/key-path perf benchmark, authenticated-endpoint enumeration list.

**Lint / Type Baseline**
- **D-15:** ruff rule families: `E`, `F`, `I`, `W`, `UP`, `B`. No `select=["ALL"]`, no `preview=true` (ROADMAP SC#5).
- **D-16:** `ruff format` rolled out whole-repo in one standalone commit, recorded in `.git-blame-ignore-revs`; CI then enforces `ruff format --check`.
- **D-17:** mypy lenient global baseline: no `disallow-untyped-defs`, `ignore_missing_imports` for untyped third-party (`mcp.*`, etc.), `check_untyped_defs` off. Zero errors day 1; tighten per-module via `[[tool.mypy.overrides]]` later (inverted strictness).
- **D-18:** Atomic commit sequence: (1) add ruff config (no code change), (2) `ruff --fix` auto-fixes, (3) `ruff format`. Each isolated and revertable.
- **D-19:** ruff pinned to an explicit version. Default: `E501`/line-length lint off (no hard-column convention); the formatter governs layout.

### Claude's Discretion
- **BASE-02 dead-code audit** of reverts `67b63e9` / `1f6c5ac`. Default: diff each revert, find orphaned imports/config/stubs, remove them; verify via `git grep` and `ruff` F401/F811.
- **bandit / pip-audit suppression conventions** — default: curated `.bandit` config + documented inline triage; high-severity must be closed or triaged.
- **Exact scorecard JSON schema** and perf-benchmark harness — default: minimal reproducible timing in the generator script.
- **mypy override granularity** for god-files — default: per-module overrides only where the global baseline would otherwise error.

### Deferred Ideas (OUT OF SCOPE)
- TOOL-05 (`datetime.utcnow()` replacement) — Phase 3.
- FE-02 (eslint + prettier for `static/`, gated in CI) — v2.
- LOG-01 (structured logging) and EXC-01 (broad-except narrowing beyond DB paths) — v2.
- Broadening the ruff rule set beyond E/F/I/W/UP/B — deferred to the no-regression ratchet in later phases.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BASE-01 | Objective baseline scorecard captured with explicit thresholds | "Scorecard" section: per-metric measurement techniques + JSON schema + ratchet enforcement |
| BASE-02 | Orphaned/dead code from reverts `67b63e9`/`1f6c5ac` audited and removed | "BASE-02 Revert Audit" section: research-time verification shows reverts are clean; verification commands provided |
| TOOL-01 | ruff lint + format adopted; clean baseline as standalone commits | "ruff" section: pinned-version config shape, zero-baseline strategy (per-file ignores vs noqa), atomic commit order |
| TOOL-02 | mypy gradual/inverted strictness; passes CI day 1 | "mypy" section: lenient global flags, `[[overrides]]` pattern, `ignore_missing_imports` for untyped 3rd-party, deterministic CI invocation |
| TOOL-03 | Dependencies pinned via committed lockfile; CI installs from it | "Lockfile" section: exact `uv pip compile` Docker recipe, ML-group handling, second-env validation |
| TOOL-04 | CI gates every PR on ruff/format/mypy/pytest/bandit/pip-audit | "GitHub Actions" section: parallel-job workflow with setup-uv, deliberate-violation proof |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Dependency resolution + pinning | Build/Repo (`requirements.in` → `.lock`) | CI (installs from lock) | Lockfile is a repo artifact; CI is its consumer/validator |
| Lint + format enforcement | Repo config (`pyproject.toml`) | CI (`ruff check`/`format --check`) | Config lives once in `pyproject.toml` (existing convention); CI runs it |
| Type checking | Repo config (`pyproject.toml [tool.mypy]`) | CI (`mypy` job) | Same single-config-file convention |
| Security scanning | CI jobs (bandit, pip-audit) | Repo config (`.bandit`, ignore files) | Scans run in CI; suppressions are versioned config |
| Measurement / scorecard | Repo script (`scripts/scorecard.py`) | CI (re-run + ratchet compare) | Re-runnable generator is source of truth; CI enforces no-regression |
| Endpoint enumeration | Repo script (reads FastAPI route table) | Scorecard consumer (Phase 2/5) | Derived from `app.routes` at import; feeds later phases |

## Standard Stack

All tools are LOCKED by CONTEXT.md. Research confirms current versions and the exact invocations.

### Core (pinned in `requirements.in` dev group + the CI workflow)
| Library | Version (verified) | Purpose | Why standard |
|---------|--------------------|---------|--------------|
| `uv` | 0.11.18 (installed locally; CI via `setup-uv@v8`) | Lockfile compile + fast installs | Locked (D-04/D-09); already installed; `pip-compile` not present |
| `ruff` | 0.15.15 (installed locally; matches PyPI latest) | Lint + format (replaces black+isort+flake8) | Locked (D-15/D-16); single fast tool |
| `mypy` | 2.1.0 (PyPI latest) | Static type checking | Locked (D-17); standard Python type checker |
| `bandit` | 1.9.4 (PyPI latest, 2026-02-25) | Python security/SAST linter | Locked (TOOL-04) |
| `pip-audit` | 2.10.0 (PyPI latest, 2025-12-01) | Dependency CVE scan against the lock | Locked (TOOL-04); PyPA-maintained |
| `pytest` + `pytest-asyncio` | already in repo (`asyncio_mode=auto`) | Behavior contract (355 files) | Existing |
| `pytest-cov` / `coverage` | NOT yet a declared dep — ADD | Per-file coverage map metric (D-14) | Needed for scorecard; flagged in Pitfall 11 |

### Supporting
| Library | Purpose | When to use |
|---------|---------|-------------|
| `setup-uv@v8` (GitHub Action) | Install uv in CI + cache | All CI jobs that install deps |
| `actions/checkout@v4` | Already used by existing workflows | Match existing workflow conventions (D-05) |
| `actions/setup-python` (optional) | uv can provide Python via `uv python install`; or use the action | uv-managed Python keeps version pinning in one tool |

### Alternatives Considered
| Instead of | Could use | Tradeoff (why NOT — decision is locked) |
|------------|-----------|------------------------------------------|
| `uv pip compile` | `pip-tools` `pip-compile` | Not installed (CONTEXT.md); uv is the chosen installer; same `requirements.in`→lock model |
| Platform-specific lock (Docker `python:3.12-slim`) | `uv pip compile --universal` | Universal lock spans platforms but is harder to make reproducible for a single Linux/3.12 CI target; D-09 locks the Docker-pinned approach for exact CI parity |
| `ruff format` | `black` | ruff format is black-compatible (double-quote default matches house style) and one fewer tool; locked |
| Per-file `# noqa` | Project-level `per-file-ignores` + `.git-blame-ignore-revs` | Prefer config-level ignores over scattering noqa (see ruff section) |

**Installation (CI / local dev tooling — NOT app runtime):**
```bash
# Pin tool versions explicitly in a dev requirements group (requirements-dev.in)
# so CI and local match (Pitfall 9 / Integration Gotchas):
ruff==0.15.15
mypy==2.1.0
bandit==1.9.4
pip-audit==2.10.0
pytest
pytest-asyncio
pytest-cov
```

**Version verification (run before finalizing the lock):**
```bash
# Inside python:3.12-slim — registry/version checks
pip index versions ruff mypy bandit pip-audit pytest-cov
```

## Package Legitimacy Audit

> All tools here are mainstream, long-established PyPI packages. slopcheck was not installed in this session; per protocol the planner SHOULD gate any *new* package install behind a `checkpoint:human-verify` task, though every package below is a well-known, high-trust project verified against PyPI this session.

| Package | Registry | Maturity | Source Repo | slopcheck | Disposition |
|---------|----------|----------|-------------|-----------|-------------|
| `uv` | PyPI / standalone | Astral, widely adopted | github.com/astral-sh/uv | n/a (not run) | Approved (locked) |
| `ruff` 0.15.15 | PyPI | Astral; latest matches installed | github.com/astral-sh/ruff | n/a | Approved (locked) |
| `mypy` 2.1.0 | PyPI | python/mypy; 2.0 released 2026-05 | github.com/python/mypy | n/a | Approved (locked) |
| `bandit` 1.9.4 | PyPI | PyCQA; 2026-02-25 | github.com/PyCQA/bandit | n/a | Approved (locked) |
| `pip-audit` 2.10.0 | PyPI | PyPA; 2025-12-01 | github.com/pypa/pip-audit | n/a | Approved (locked) |
| `pytest-cov` | PyPI | pytest-dev, mature | github.com/pytest-dev/pytest-cov | n/a | Approved (new dep — minor) |

**Packages removed (SLOP):** none.
**Packages flagged (SUS):** none.
*Note: these are dev/CI tools, not app-runtime deps; they do not enter the core `requirements.lock` unless deliberately added to a dev group. Keeping them OUT of the core runtime lock keeps the runtime install surface unchanged (behavior-preserving).*

## Architecture Patterns

### System Architecture Diagram (the Phase 1 instrumentation pipeline)

```
                         requirements.in  (NEW: direct core deps only)
                         requirements-optional.in (NEW: AGPL PyMuPDF + markitdown etc.)
                                  │
                  docker run python:3.12-slim
                  uv pip compile --generate-hashes
                                  │
                                  ▼
            requirements.lock  +  requirements-optional.lock   (committed)
                                  │
                ┌─────────────────┼──────────────────────────────────┐
                ▼                 ▼                                    ▼
        second clean         CI install                         local dev install
        container            (setup-uv@v8,                      (uv pip sync)
        pip install -r       uv pip sync --require-hashes)
        (SC#3 proof)              │
                                  ▼
   ┌──────────────────── GitHub Actions: quality-gate.yml ────────────────────┐
   │  on: pull_request + push:main   (parallel jobs, each hard-blocks)         │
   │   job:lint    → ruff check .                                              │
   │   job:format  → ruff format --check .                                     │
   │   job:mypy    → mypy . (lenient global + overrides)                       │
   │   job:pytest  → pytest (+ pytest-cov)                                     │
   │   job:bandit  → bandit -c pyproject.toml -r src routes core ...           │
   │   job:pipaudit→ pip-audit -r requirements.lock                            │
   │   job:scorecard → python scripts/scorecard.py --check (ratchet regress?)  │
   └──────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
            .planning/scorecard/baseline.json  (source of truth)
            .planning/scorecard/SCORECARD.md   (rendered view)
```

The diagram traces the primary path: `requirements.in` → compile-in-Docker → committed lock → consumed by both a second-env validation and CI → CI runs six parallel gates + the scorecard ratchet.

### Recommended Repo Structure (new/changed files)
```
requirements.in                 # NEW — direct core deps (intent), no pins
requirements-optional.in        # NEW — AGPL/optional intent (D-08)
requirements.lock               # NEW — uv-compiled, hashed, committed (D-07/D-09)
requirements-optional.lock      # NEW — optional lock (D-08)
requirements-dev.in / .lock     # NEW — pinned tool versions (ruff/mypy/bandit/...)
pyproject.toml                  # EXTEND — add [tool.ruff], [tool.mypy], [tool.coverage], [tool.bandit?]
.bandit                         # NEW (or [tool.bandit] in pyproject) — curated suppressions
.git-blame-ignore-revs          # NEW — records the ruff format bulk-reformat commit (D-16)
scripts/scorecard.py            # NEW — re-runnable metric generator (D-12)
.planning/scorecard/baseline.json   # NEW — source of truth (D-10/D-13)
.planning/scorecard/SCORECARD.md    # NEW — rendered markdown view
.github/workflows/quality-gate.yml  # NEW — parallel CI gates (D-02/D-05)
```

### Pattern 1: pip-tools-style intent/resolution separation (D-07)
**What:** Hand-author `requirements.in` (the ~30 direct deps currently in `requirements.txt`), compile to a fully-pinned, hashed `requirements.lock`. `requirements.txt` today already lists only direct deps with loose pins — it is effectively the `.in` content; the new `.in` makes intent explicit and the `.lock` adds the resolved transitive graph + hashes.
**When to use:** Always for this repo — `pip freeze` is explicitly forbidden (Pitfall 10 / tech-debt table).

### Pattern 2: Single `pyproject.toml` tool config (existing convention)
**What:** All tool config (ruff, mypy, coverage, pytest, optionally bandit) lives in `pyproject.toml`, which already holds pytest config. CONVENTIONS.md + CONTEXT.md confirm: extend this one file, do not scatter `ruff.toml`/`mypy.ini`/`.flake8`.
**When to use:** All Phase 1 tool config.

### Pattern 3: Inverted (per-module) mypy strictness (D-17)
**What:** Global config is lenient (zero errors day 1). `[[tool.mypy.overrides]]` blocks tighten checking per-module as files get typed in later phases. This is the standard brownfield mypy adoption strategy (Pitfall 8).
**When to use:** Day-1 baseline = lenient only. Phase 3+ adds stricter overrides per split module.

### Anti-Patterns to Avoid
- **`select = ["ALL"]` or `preview = true` in ruff** — every ruff bump breaks CI (Pitfall 9, locked against by D-15).
- **`mypy --strict` globally** — hundreds of false positives on lazy-imports/untyped code (Pitfall 8).
- **`pip freeze` as the lock** — platform-specific, not reproducible (Pitfall 10).
- **Adding tools to the *runtime* lock** — dev tools (ruff/mypy/bandit) must stay out of `requirements.lock` so the app's runtime install surface is unchanged (behavior-preserving). Use a separate dev group.
- **Scattering `# noqa` to reach zero** — prefer config-level `per-file-ignores`; reserve `# noqa[CODE]` for one-off, commented exceptions.

## Don't Hand-Roll

| Problem | Don't build | Use instead | Why |
|---------|-------------|-------------|-----|
| Transitive dep resolution + hash pinning | A `pip freeze` script | `uv pip compile --generate-hashes` | freeze captures the dirty env, not a clean resolved graph; no cross-platform guarantee (Pitfall 10) |
| Import sorting | A custom isort config | ruff `I` rules | ruff's `I` is isort-compatible and already in the locked rule set |
| Line counting per module | `wc -l` shell glob | `ruff` provides `--statistics`; for LOC use a small Python `pathlib` walker (deterministic, excludes vendored dirs) | Need reproducibility + exclusion rules; trivial in `scorecard.py` |
| Coverage measurement | Parsing pytest output | `pytest-cov` + `coverage json` | Machine-readable per-file map feeds the scorecard directly |
| Endpoint enumeration | grep for `@router` decorators | Import `app` and walk `app.routes` (FastAPI `APIRoute` objects expose `.path`, `.methods`, `.endpoint`) | The app builds 41 routers via `include_router`; the route table is the ground truth, grep misses dynamic registration |
| CVE scanning | Curl-ing an advisory DB | `pip-audit -r requirements.lock` | PyPA-maintained, queries OSV/PyPI advisory DB, understands the lock format |

**Key insight:** Everything in this phase has a mature, single-purpose tool. The only *bespoke* code is `scripts/scorecard.py`, which orchestrates these tools' machine-readable outputs (ruff `--output-format=json`, `coverage json`, `mypy` stats, FastAPI route table) into one JSON document — it should shell out to the tools, not reimplement them.

## ruff: reaching a true zero-finding baseline (TOOL-01, D-15/D-16/D-18/D-19)

**Pinned version:** `ruff==0.15.15` (matches the locally-installed version; pin in the dev group, ROADMAP SC#5).

### `pyproject.toml` config shape
```toml
[tool.ruff]
# Match the existing snake_case / no-hard-column house style.
target-version = "py312"          # supported/shipped runtime (Dockerfile python:3.12-slim)
line-length = 88                  # formatter default; lint E501 is disabled below so this
                                  # only governs `ruff format` wrapping, not lint failures
extend-exclude = [                # do not lint/format vendored or generated trees
    "static/",                    # frontend JS (out of scope; FE-02 is v2)
    "data/", "logs/",
]

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "B"]   # LOCKED rule families (D-15)
ignore = [
    "E501",   # line length — no hard column convention (D-19, CONVENTIONS.md)
    # add specific codes here ONLY if a whole-repo auto-fix is infeasible AND
    # a per-file-ignore is too broad; document each with a comment.
]
# NO preview = true ; NO select = ["ALL"]  (ROADMAP SC#5)

[tool.ruff.lint.per-file-ignores]
# Tests legitimately import private helpers and stub modules; relax there.
"tests/**" = ["F401", "F811"]     # only if baseline findings warrant it — measure first
"conftest.py" = ["F401"]

[tool.ruff.format]
quote-style = "double"            # matches house style (CONVENTIONS.md)
# (defaults otherwise; ruff format is black-compatible)
```

### Zero-baseline strategy (the actual work)
1. **Measure first** — in a clean checkout run:
   ```bash
   ruff check . --output-format=json > /tmp/ruff-before.json
   ruff check . --statistics          # human view of counts per rule
   ```
2. **Auto-fix what's safe** (D-18 commit 2):
   ```bash
   ruff check . --fix                 # applies safe fixes (I import-sort, UP rewrites, some F/B)
   ruff check . --fix --unsafe-fixes  # OPTIONAL, review diff carefully; many B/UP need this
   ```
   Run the full pytest suite immediately after — `--unsafe-fixes` can change semantics (e.g. some `UP` rewrites). Only keep unsafe fixes if the suite stays green.
3. **Residual findings** — for the handful that auto-fix cannot resolve:
   - Prefer **config-level `per-file-ignores`** (versioned, greppable, no code churn) over inline `# noqa`.
   - Use inline `# noqa: CODE  # reason` ONLY for genuinely-one-off, intentional cases (e.g. an `F401` re-export in a barrel `__init__.py` — though those are rare here since `src/`/`routes/` don't use barrels).
   - Never bare `# noqa` (Pitfall 9 / tech-debt table).
4. **Format** (D-18 commit 3): `ruff format .` — one standalone commit, then record it:
   ```bash
   git rev-parse HEAD >> .git-blame-ignore-revs   # add the format commit SHA
   ```
   Configure local git + tell reviewers: `git config blame.ignoreRevsFile .git-blame-ignore-revs` (GitHub honors `.git-blame-ignore-revs` automatically).

### Interplay note
`ruff format` defaults to double quotes (matches house style) and an 88-col wrap. Disabling lint `E501` means the **formatter** decides wrapping but the **linter** never fails on length — exactly the locked intent (D-19). The format-check job (`ruff format --check`) then enforces the formatter's output is stable.

### CI invocation (deterministic)
```bash
ruff check --output-format=github .       # annotates PRs inline; nonzero exit = fail
ruff format --check .                      # separate job (D-02); nonzero exit = fail
```

## mypy: inverted-strictness, zero-error day 1 (TOOL-02, D-17)

**Pinned version:** `mypy==2.1.0`.

### `pyproject.toml` config shape
```toml
[tool.mypy]
python_version = "3.12"
# --- Lenient global baseline (zero errors day 1) ---
ignore_missing_imports = true       # blanket fallback for untyped 3rd-party
check_untyped_defs = false          # don't type-check bodies of untyped funcs (D-17)
disallow_untyped_defs = false       # do NOT require annotations (D-17)
disallow_incomplete_defs = false
warn_return_any = false
warn_unused_ignores = true          # keeps `# type: ignore` honest (cheap, no false errors)
warn_redundant_casts = true
no_implicit_optional = false        # legacy implicit-Optional tolerated at baseline
exclude = [                         # keep scope to backend Python
    "^static/",
    "^tests/",                      # optional — tests are heavily stubbed; exclude to keep day-1 green
    "^scripts/",
]
# Determinism: avoid the incremental cache being the source of CI flakiness.
# In CI run with --no-incremental (see invocation) OR commit no .mypy_cache.

# --- Per-module overrides for known-untyped third parties (explicit, greppable) ---
# Even with global ignore_missing_imports=true, list the big ones explicitly so the
# intent is documented and the global flag can be tightened later without surprise.
[[tool.mypy.overrides]]
module = [
    "mcp.*", "chromadb.*", "fastembed.*", "caldav.*", "icalendar.*",
    "croniter.*", "qrcode.*", "pyotp.*", "fitz.*", "markitdown.*",
    "duckduckgo_search.*", "faster_whisper.*", "youtube_transcript_api.*",
]
ignore_missing_imports = true

# --- Inverted strictness: tighten per-module as files are typed (Phase 3+) ---
# Day 1 this list is EMPTY or near-empty. Example shape for later phases:
# [[tool.mypy.overrides]]
# module = ["src.tools.search", "core.atomic_io"]
# disallow_untyped_defs = true
# check_untyped_defs = true
```

### Reaching zero on day 1
- Run `mypy .` (respecting `exclude`) against the lenient config. Expect a small residual of real errors even when lenient — e.g. genuine redefinitions, bad imports, `None`-attribute on a known type.
- For each residual: prefer a **targeted `# type: ignore[error-code]` with a comment** (never bare ignore). If a whole module is hopeless at baseline, add a temporary per-module override that relaxes the *specific* failing flag — and log it as a tracking item so the ratchet can close it later.
- `mypy --stats` (or count typed defs — see scorecard) produces the **mypy-typed %** baseline metric.

### Deterministic CI invocation
```bash
mypy --no-incremental --no-error-summary --config-file pyproject.toml .
# --no-incremental: no cache → identical result every run (avoids stale-cache CI flakiness)
# nonzero exit on any error = fail
```
Pin the mypy version (dev group) so a mypy bump can't silently introduce new errors (analogous to Pitfall 9 for ruff).

## Lockfile: uv pip compile inside python:3.12-slim (TOOL-03, D-06..D-09)

### The reproducible Docker recipe
The lock MUST be generated in the CI base image, NOT on the local Python 3.14 host (ROADMAP phase note — local-vs-CI drift is the explicit risk).

```bash
# From repo root. Mounts the repo, runs uv inside python:3.12-slim, writes the lock.
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c '
  set -euo pipefail
  pip install --no-cache-dir uv==0.11.18
  # Core unified lock (D-06/D-07/D-09): single resolution incl. the ML group.
  uv pip compile requirements.in \
      --generate-hashes \
      --python-version 3.12 \
      --output-file requirements.lock
  # Optional/AGPL lock (D-08): separate file, also hashed.
  uv pip compile requirements-optional.in \
      --generate-hashes \
      --python-version 3.12 \
      --output-file requirements-optional.lock
'
```
Notes (verified via `uv pip compile --help`, uv 0.11.18):
- `--generate-hashes` emits `--hash=sha256:...` lines → enables `--require-hashes` installs (supply-chain guarantee, D-09).
- `--python-version 3.12` constrains the resolution markers to the shipped runtime; running *inside* `python:3.12-slim` additionally means any platform-specific wheel selection (onnxruntime, numpy) matches the linux/glibc target CI uses.
- Do **not** use `--universal` here — the locked decision is a Docker-pinned, single-platform (linux/3.12) lock for exact CI parity. `--universal` is the alternative only if a multi-platform lock is ever needed (out of scope).
- `requirements.in` content = the direct deps currently in `requirements.txt` minus `pytest`/`pytest-asyncio` (move those to `requirements-dev.in`). Keep `markitdown[...]==0.1.5` in the **optional** `.in` (it currently lives in `requirements-optional.txt`).

### The ML-group conflict (Pitfall 10) — what triggers it and how to recover
- **Risk surface:** `chromadb-client` + `fastembed` + (transitively) `onnxruntime`, plus `markitdown`'s `magika` which also pulls `onnxruntime`. A unified resolve can hit `ResolutionImpossible` if two of these pin incompatible `onnxruntime`/`numpy`/`protobuf` ranges.
- **Detection:** `uv pip compile` fails fast with an explicit `× No solution found` / conflicting-version message naming the packages. Capture stderr.
- **Recovery (D-06 fallback, only if unified fails):**
  1. Pin the shared transitive explicitly in `requirements.in` (e.g. add `onnxruntime==<version compatible with both>` and `numpy==<...>`) and recompile — often resolves it.
  2. If still impossible, split a `requirements-ml.in` (chromadb-client + fastembed) → `requirements-ml.lock`, keep the core lock without them, and have CI install core + ml separately. This also exercises the keyword-fallback degradation path (CONCERNS.md). Document the split rationale in a comment.
- Note: `markitdown==0.1.5` lives in the **optional** lock (D-08), so its `onnxruntime` constraint only collides with the core ML group if both locks are installed together. Verify the optional lock resolves on top of (or independently of) the core lock.

### Validating the lock (ROADMAP SC#3)
```bash
# Second clean container — proves reproducibility, no ResolutionImpossible.
docker run --rm -v "$PWD":/w -w /w python:3.12-slim bash -c '
  pip install --require-hashes --no-cache-dir -r requirements.lock
'
# Exit 0 = SC#3 satisfied. (Use --require-hashes since the lock has hashes.)
```
CI's pytest/lint/etc. jobs install from this lock the same way (via uv): `uv pip install --require-hashes -r requirements.lock` (or `uv pip sync requirements.lock`).

## GitHub Actions: parallel quality gate (TOOL-04, D-01..D-05)

**File:** `.github/workflows/quality-gate.yml` (NEW; do not touch the two doc-check workflows — D-05).
**Triggers:** `pull_request` + `push: { branches: [main] }` (D-03).
**Shape:** one job per gate (D-02), each independent and hard-blocking. Make all six required status checks in branch protection so a failure blocks merge (D-01).

```yaml
name: ci / quality gate

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system ruff==0.15.15
      - run: ruff check --output-format=github .

  format:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system ruff==0.15.15
      - run: ruff format --check .

  mypy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      # mypy needs the deps installed to resolve imports it DOESN'T ignore.
      - run: uv pip install --system --require-hashes -r requirements.lock
      - run: uv pip install --system mypy==2.1.0
      - run: mypy --no-incremental --config-file pyproject.toml .

  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system --require-hashes -r requirements.lock
      - run: uv pip install --system pytest-cov
      - run: pytest            # asyncio_mode=auto already in pyproject

  bandit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system bandit==1.9.4
      - run: bandit -c pyproject.toml -r app.py src routes core mcp_servers companion services

  pip-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system pip-audit==2.10.0
      - run: pip-audit -r requirements.lock     # add --ignore-vuln GHSA-... per triage

  scorecard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
        with: { version: "0.11.18", enable-cache: true }
      - run: uv pip install --system --require-hashes -r requirements.lock
      - run: uv pip install --system ruff==0.15.15 mypy==2.1.0 bandit==1.9.4 pytest-cov
      - run: python scripts/scorecard.py --check   # exit nonzero if any metric regressed
```

### Proving the gate works (ROADMAP SC#1)
Open a throwaway PR that introduces a *deliberate* violation (an unused import for `lint` F401; a formatting change for `format`; a type error for `mypy`; a `assert False`-style failing test for `pytest`; a `subprocess(shell=True)` for `bandit`). Confirm the corresponding required check goes red and blocks merge. Then close the PR. This is the SC#1 acceptance demonstration — script it as a documented manual verification step, not a committed test.

### bandit + pip-audit suppression conventions (Claude's-discretion default)
- **bandit:** configure in `pyproject.toml [tool.bandit]` (bandit 1.9 reads `pyproject.toml`) OR a `.bandit` file. Curate `skips` for known-intentional patterns — this repo's `THREAT_MODEL.md` explicitly treats shell/file/email as intentional admin features, so `B602/B603/B604/B605/B607` (subprocess) and similar will fire heavily and must be triaged into documented skips with rationale, not blanket-disabled. Run once as an audit, classify each finding, encode the suppressions.
  ```toml
  [tool.bandit]
  exclude_dirs = ["tests", "static", "data", "logs"]
  # skips = ["B404", "B603"]  # populate with documented rationale after the audit pass
  ```
- **pip-audit:** maintain a documented ignore list for non-applicable CVEs:
  ```bash
  pip-audit -r requirements.lock --ignore-vuln GHSA-xxxx-...  # one per known-triaged CVE
  ```
  High/critical CVEs must be resolved (bump the pin) or carry a written triage entry. Keep the triage notes in a committed file (e.g. `.planning/scorecard/security-triage.md`) referenced by the scorecard's security-findings list.

## Scorecard: metrics, schema, and the ratchet (BASE-01, D-10..D-14)

`scripts/scorecard.py` is the only bespoke code. It shells out to the mature tools and assembles a JSON document. It supports two modes: `--write` (regenerate `baseline.json` + `SCORECARD.md`) and `--check` (regenerate metrics, compare to committed baseline, exit nonzero on regression — the CI ratchet, D-11).

### Per-metric measurement technique (reproducible)
| Metric (D-14) | How to compute | Tool/source |
|---------------|----------------|-------------|
| Per-module line counts | Walk `src/`, `routes/`, `core/`, `services/`, `mcp_servers/`, `companion/`, `app.py` with `pathlib`; count physical lines per `.py`; exclude `tests/`, `static/`, vendored | stdlib (deterministic; document the include/exclude set) |
| mypy-typed % | Count annotated function defs ÷ total function defs. Either parse `mypy --html-report`/`--linecount-report`, or AST-walk: for each `ast.FunctionDef`/`AsyncFunctionDef`, "typed" = has return annotation AND all non-`self`/`cls` args annotated. AST is the most reproducible and tool-version-independent. | `ast` module (preferred) or `mypy --linecount-report` |
| ruff finding count | `ruff check . --output-format=json` → `len(findings)`; also break down by rule code | `ruff` (pinned) |
| Security findings list | `bandit ... -f json` results (post-suppression) + `pip-audit -r requirements.lock -f json` results, merged into a list with severity | `bandit`, `pip-audit` |
| Per-file test-coverage map | `pytest --cov=. --cov-report=json` → `coverage.json` `files{}` map of per-file percent + missing lines | `pytest-cov` / `coverage json` |
| Startup / key-path perf benchmark | Measure cold-import + app-construction time without serving traffic: `python -c "import time;t=time.perf_counter();import app;print(time.perf_counter()-t)"` averaged over N runs; optionally TestClient one cheap GET (e.g. `/api/health` if present) for a key-path latency sample. Record machine/python/run-count metadata. Behavior-preserving: only times existing code, changes nothing. | stdlib `time`, `fastapi.testclient` |
| Authenticated-endpoint enumeration | Import `app`, walk `app.routes` for `APIRoute`s; for each, record `path`, sorted `methods`, and a best-effort auth classification (auth is enforced by an inline `AuthMiddleware` in `app.py` with a small public allow-list, NOT per-route `Depends`, so classification = "all routes except the allow-list"). Emit the list; this is the input for Phase 2 COV-03 / Phase 5 SEC-01. | FastAPI route table + the inline allow-list constants in `app.py:162-194` |

> Auth-model note (CORRECTED by pattern-mapper + plan-checker): the allow-list is **inline in `app.py:162-194`** (`AUTH_EXEMPT_EXACT` / `AUTH_EXEMPT_PREFIXES` / `AUTH_EXEMPT_PATTERNS` / `_is_auth_exempt`), gated behind `if AUTH_ENABLED:` — NOT in `core/middleware.py` (which only holds `require_admin` + `SecurityHeadersMiddleware` and has no allow-list). The enumeration script must encode the allow-list VALUES from `app.py:162-194` verbatim (not import the AUTH_ENABLED-gated symbols) and mark everything else authenticated, rather than scanning for `Depends`.

### JSON schema shape (default — finalize in planning)
```json
{
  "schema_version": 1,
  "generated_at": "2026-06-03T00:00:00Z",
  "git_sha": "<HEAD sha>",
  "tool_versions": { "ruff": "0.15.15", "mypy": "2.1.0", "bandit": "1.9.4", "pip_audit": "2.10.0" },
  "metrics": {
    "module_line_counts": { "src/tool_implementations.py": 4144, "...": 0 },
    "max_module_loc": 4144,
    "typed_pct": { "overall": 0.0, "by_module": { "core/atomic_io.py": 1.0 } },
    "ruff_findings": { "total": 0, "by_code": {} },
    "security_findings": [ { "tool": "bandit", "id": "B603", "severity": "...", "file": "...", "triaged": true } ],
    "coverage": { "overall_pct": 0.0, "by_file": { "src/tool_implementations.py": 12.3 } },
    "perf": { "import_app_seconds_mean": 0.0, "runs": 5, "python": "3.12", "key_path_ms": null },
    "auth_endpoints": [ { "path": "/api/email/accounts", "methods": ["GET"], "authenticated": true } ]
  },
  "thresholds": {
    "mode": "ratchet",
    "ruff_findings_total": { "max": 0 },
    "typed_pct_overall": { "min": 0.0 },
    "max_module_loc": { "max": 4144 },
    "coverage_overall_pct": { "min": 0.0 },
    "security_high_critical": { "max": 0 }
  }
}
```

### Ratchet enforcement (D-11)
`--check` recomputes each metric and compares to `thresholds`:
- `ruff_findings_total` must be `<= max` (0 at baseline → can only stay 0 or fail).
- `typed_pct_overall` must be `>= min` (can only rise).
- `max_module_loc` must be `<= max` (god-files can only shrink, not grow — protects later phases).
- `coverage_overall_pct` must be `>= min`.
- `security_high_critical` must be `<= max` (0).
Any regression → nonzero exit → the CI `scorecard` job fails the PR. Baseline values are written by `--write` (run once at phase end) and committed as `baseline.json`. A few absolute targets (zero ruff findings, zero high/critical CVEs) are encoded directly; everything else is relative-to-baseline (D-11).

## BASE-02: Revert Audit — VERIFIED CLEAN this session

Research-time verification (`git show --stat` + `git grep`) of both reverts:

- **`67b63e9` Revert "fix(ui): allow manual prompt bar resize (#1201)"** — reverted 3 files (`static/js/ui.js`, `static/style.css`, `tests/test_prompt_bar_manual_resize.py`). The test file is gone; `ui.js` has no orphaned resize handler (remaining `resize` hits are the unrelated auto-resize-textarea feature + calendar pane). The one `style.css` `manually-resized` hit is an unrelated comment on the model-compare selector. **No orphans.**
- **`1f6c5ac` Revert "Codex Agent integration..."** — reverted 8 files including deleting `routes/codex_routes.py` and `integrations/codex/`. Verified: `integrations/codex/` directory is gone, `routes/codex_routes.py` is gone, `app.py` has zero `codex` references, `routes/api_token_routes.py` and `static/js/settings.js` have zero `codex`/`plugin` remnants. The only `codex` grep hits live in `routes/model_routes.py:345-346` and are **unrelated** (OpenAI model-name filter strings `-codex`/`codex-`, predating and independent of the reverted integration). **No orphans.**

**Implication for planning:** BASE-02 is largely a *verification* task, not a removal task — the reverts were clean. The plan should still:
1. Run the audit commands as evidence (below) and record the result in the scorecard/security-findings or a short audit note.
2. After ruff config lands, confirm `ruff check` reports zero `F401`/`F811` attributable to revert remnants (ROADMAP SC#4) — this is automatically satisfied by the zero-finding baseline.

**Audit / verification commands (ROADMAP SC#4):**
```bash
git show 67b63e9 --stat        # confirm reverted file set
git show 1f6c5ac --stat
git grep -n -i 'codex' -- app.py routes/ static/js/ | grep -vi 'model_routes'   # expect empty
git grep -n 'promptBarResize\|prompt-bar-resize\|manual.*prompt.*resize' -- static/   # expect empty
ls integrations/codex routes/codex_routes.py 2>/dev/null    # expect "No such file"
ruff check . --select F401,F811    # expect zero (part of clean baseline)
```

## Sequencing / Coupling Risks (the core planning question)

The two file-touching tools (`ruff --fix`, `ruff format`) will modify many of the 560 `.py` files. The 355-file pytest suite is the green-gate. Recommended atomic sequence — each step is independently committable and verifiable; run the **full pytest suite after every code-touching step**:

| # | Step | Touches code? | Verify | Revertable unit |
|---|------|---------------|--------|-----------------|
| 1 | Create `requirements.in`/`-optional.in`, compile locks in Docker, commit locks | No (new files) | SC#3 second-container install passes | lockfile commit |
| 2 | Add `[tool.ruff]` config to `pyproject.toml` (no code change) | No | `ruff check .` runs (findings expected, not yet zero) | config commit (D-18.1) |
| 3 | `ruff check . --fix` (+ reviewed `--unsafe-fixes`) | Yes (many files) | **full pytest green**; `ruff check .` finding count drops | fix commit (D-18.2) |
| 4 | `ruff format .` whole-repo; append SHA to `.git-blame-ignore-revs` | Yes (whitespace/layout) | **full pytest green**; `ruff format --check .` clean | format commit (D-18.3) |
| 5 | Resolve residual ruff findings via `per-file-ignores`/targeted noqa → zero | Minimal | `ruff check .` == 0 findings | small commit |
| 6 | Add `[tool.mypy]` lenient config; resolve residual to zero errors | Minimal (targeted ignores) | `mypy --no-incremental .` == 0 errors | config commit |
| 7 | Add `scripts/scorecard.py`; run `--write` to produce `baseline.json` + `SCORECARD.md` | No (new files) | scorecard regenerates deterministically | scorecard commit |
| 8 | Add `.github/workflows/quality-gate.yml`; set required checks | No (CI) | deliberate-violation PR fails each gate (SC#1) | workflow commit |
| 9 | BASE-02 audit note (reverts already clean) | No | audit commands return empty (SC#4) | doc commit |

**Why this order:** ruff config before mypy config (mypy is cleaner to zero on already-import-sorted, formatted code). Format AFTER `--fix` (fixing first avoids reformatting code that's about to be deleted/rewritten). Scorecard baseline is captured AFTER the repo is clean (so `ruff_findings=0` and `typed_pct` reflect the post-cleanup state that CI will ratchet against). CI workflow last (it consumes the lock, configs, and scorecard — wiring it earlier would red-flag an as-yet-unclean repo, violating D-01's clean-baseline-first posture).

**Coupling caution:** `--unsafe-fixes` (some `UP`/`B` rewrites) can alter runtime semantics. Apply them in a *separate* sub-commit from safe fixes if volume is high, run the full suite, and be ready to drop individual unsafe fixes that break a test. Do NOT bundle unsafe fixes with the formatting commit — keep the blame-ignore commit purely mechanical whitespace so blame attribution stays accurate.

## Validation Architecture

> Nyquist validation is enabled. This section maps each success criterion to the minimum verification set so a VALIDATION.md can be derived.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio (`asyncio_mode = "auto"`), already configured in `pyproject.toml` |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths=["tests"]`) |
| Quick run command | `pytest -q` (subset) — for per-step gating after ruff fix/format |
| Full suite command | `pytest` (355 test files — the behavior contract) |
| New dep needed | `pytest-cov` (for the coverage-map metric; not yet declared — Wave 0) |

### Phase Requirements → Verification Map
| Req | Behavior to prove | Verification type | Command / method |
|-----|-------------------|-------------------|------------------|
| TOOL-03 / SC#3 | Lock installs clean on a 2nd clean Linux env, no `ResolutionImpossible` | integration | `docker run --rm ... python:3.12-slim pip install --require-hashes -r requirements.lock` → exit 0 |
| TOOL-01 / SC#5 | ruff zero findings, pinned version, no ALL/preview | automated | `ruff check .` exits 0; assert `pyproject.toml` has no `select=["ALL"]` / `preview=true`; ruff pinned in dev lock |
| TOOL-01 / SC#1 | format is stable | automated | `ruff format --check .` exits 0 |
| TOOL-02 / SC#5 | mypy zero errors, lenient global + overrides | automated | `mypy --no-incremental .` exits 0; assert overrides present, no global `strict=true` |
| TOOL-04 / SC#1 | Each gate hard-blocks; a deliberate violation fails the PR check | manual (documented) | Throwaway PR injecting one violation per gate; confirm each required check goes red |
| BASE-01 / SC#2 | All 7 metrics measured + thresholds recorded; ratchet enforces | automated | `python scripts/scorecard.py --write` produces complete `baseline.json`; `--check` exits nonzero on a seeded regression |
| BASE-02 / SC#4 | No dead code from the two reverts | automated + manual | the audit grep/ls commands return empty; `ruff check --select F401,F811` == 0 |
| Pitfall 11 | Suite is stable under `--cov` instrumentation | automated | `pytest --cov=.` runs green and deterministic before the coverage gate is trusted |

### Sampling Rate
- **Per file-touching step (ruff fix, ruff format):** full `pytest` (the behavior contract — D-16/Pitfall guidance).
- **Per other step:** the relevant single gate command (ruff/mypy/scorecard).
- **Phase gate:** all six CI jobs green on a real PR + the deliberate-violation demonstration (SC#1) + the SC#3 second-container install.

### Wave 0 Gaps
- [ ] `requirements-dev.in` / pinned dev tool versions — establish before CI references them.
- [ ] `pytest-cov` install + a one-time `pytest --cov=.` stability run (Pitfall 11) BEFORE wiring the coverage metric, with `concurrency = "greenlet,thread"` in `[tool.coverage.run]` if async flakiness appears.
- [ ] `scripts/scorecard.py` does not exist — build it (the only bespoke code).
- [ ] Confirm a cheap unauthenticated key-path endpoint exists for the perf sample (e.g. health) or fall back to import-time-only timing.
- *(No new pytest test files are strictly required by Phase 1 — the existing suite is the contract. Coverage *gap-fill* is Phase 2, explicitly out of scope here.)*

## Security Domain

> `security_enforcement` not explicitly disabled in config → included. Note: this phase ADDS the security tooling (bandit, pip-audit); the systematic ASVS audit itself is Phase 5 (SEC-*). Phase 1's security surface is the tooling + the supply-chain lock.

### Applicable ASVS Categories (Phase 1 scope only)
| ASVS Category | Applies | Standard control (Phase 1) |
|---------------|---------|----------------------------|
| V1 Architecture / SDLC | yes | CI quality gates + pinned lock institute the secure-build pipeline |
| V5 Input Validation | no (Phase 5) | — |
| V6 Cryptography | no (Phase 5 SEC-04) | — |
| V10 Malicious Code / Supply Chain | yes | `--generate-hashes` lock + `--require-hashes` install + `pip-audit` CVE gate + slopcheck-style discipline for any new dep |
| V14 Configuration | yes | bandit config-as-code; documented suppressions with rationale |

### Known Threat Patterns for this phase
| Pattern | STRIDE | Mitigation (Phase 1) |
|---------|--------|----------------------|
| Dependency substitution / unpinned drift | Tampering | Hash-pinned lock; `--require-hashes` install; pip-audit gate |
| Slopsquatted/new transitive package entering the lock | Tampering | Review the compiled lock diff; pip-audit; keep dev tools out of runtime lock |
| Known-CVE dependency | Information disclosure / EoP | `pip-audit -r requirements.lock`; high/critical block merge or carry triage |
| Insecure code patterns (subprocess shell, hardcoded secrets) | EoP / Info disclosure | bandit audit pass → curated, documented suppressions (intentional admin features per THREAT_MODEL.md are triaged, not blanket-ignored) |

## State of the Art

| Old approach | Current approach | Impact |
|--------------|------------------|--------|
| black + isort + flake8 (3 tools) | ruff (lint + format, one tool) | Single pinned version; matches house double-quote style; locked |
| `pip-compile` (pip-tools) | `uv pip compile` | Faster, same `.in`→`.lock` model; uv already installed |
| mypy 1.x | mypy 2.1.0 (2.0 released 2026-05) | mypyc-accelerated; behavior for brownfield lenient config unchanged in practice |
| `setup-uv@v3/v4` | `setup-uv@v8` | Built-in caching (`enable-cache: true`); pin uv `version` for determinism |

**Deprecated/outdated:**
- `pip freeze` as a lockfile — non-reproducible across platforms (Pitfall 10); use `uv pip compile`.
- `mypy --strict` global on brownfield — replaced by inverted per-module overrides (Pitfall 8).

## Assumptions Log

| # | Claim | Section | Risk if wrong |
|---|-------|---------|---------------|
| A1 | Unified core resolution will converge in `python:3.12-slim` (no ML split needed) | Lockfile | LOW–MED — D-06 fallback (split `requirements-ml`) is pre-planned; only adds a step |
| A2 | A cheap unauthenticated health/key-path endpoint exists for the perf sample | Scorecard perf metric | LOW — fall back to import-time-only timing; perf is a guardrail not a gate |
| A3 | mypy reaches zero errors with the lenient config + a small set of targeted `# type: ignore[code]` | mypy | LOW–MED — may need a few per-module relaxations; inverted-strictness pattern absorbs this |
| A4 | `--unsafe-fixes` will be needed for some B/UP findings and may touch semantics | ruff sequencing | LOW — applied in a separate reviewed commit, gated by full pytest |
| A5 | bandit will fire heavily on intentional admin subprocess/file features and need curated skips | bandit suppression | LOW — expected per THREAT_MODEL.md; the audit pass classifies them |
| A6 | mypy version is 2.1.0 (mypy went 2.0 in May 2026) | Standard Stack | LOW — verified against PyPI + changelog this session |

## Open Questions (RESOLVED)

1. **Unified vs split ML lock** — Will `chromadb-client + fastembed + onnxruntime + (optional) markitdown/magika` resolve together in `python:3.12-slim`?
   - Known: it is the documented conflict hotspot (Pitfall 10).
   - Unclear: actual resolution outcome — only determinable by running the compile.
   - **RESOLVED (adopted in Plan 01-01 Task 2):** first plan task is the Docker compile; branch to the D-06 fallback (split `requirements-ml.in`) only if it fails. Capture the stderr either way for the scorecard/audit record.
2. **mypy scope of `tests/`** — Include or exclude tests from mypy day 1?
   - **RESOLVED (adopted in Plan 01-03):** exclude `tests/` initially (heavily stubbed via `conftest.py` `MagicMock` injection — typing them yields noise). Revisit in a later phase via the ratchet.
3. **Exact `typed_pct` definition** — `mypy --linecount-report` vs AST-based annotated-def ratio.
   - **RESOLVED (adopted in Plan 01-04 Task 1):** AST-based (tool-version-independent, fully reproducible, no dependence on mypy's report format which can change across versions). Document the exact rule in `scripts/scorecard.py`.

## Environment Availability

| Dependency | Required by | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| `uv` | Lockfile compile + CI installs | ✓ (local) | 0.11.18 | `setup-uv@v8` in CI |
| `ruff` | Lint/format | ✓ (local) | 0.15.15 | install via uv |
| `mypy` | Type check | ✗ (not a module locally) | — (2.1.0 target) | install via uv in dev group / CI |
| `bandit` | Security scan | ✗ | — (1.9.4 target) | install via uv |
| `pip-audit` | CVE scan | ✗ | — (2.10.0 target) | install via uv |
| `pytest-cov` | Coverage metric | ✗ | — | install via uv (Wave 0) |
| Docker + `python:3.12-slim` | Lockfile generation parity | assume ✓ (CI base image; Dockerfile uses it) | 3.12 | none — required for D-09 reproducibility |
| Local Python | host dev | ✓ | 3.14.5 | NOT used for lock generation (drift risk) |

**Missing with no fallback:** Docker access to pull `python:3.12-slim` is required for D-09 lock generation and SC#3 validation — confirm the planning/execution environment can run Docker.
**Missing with fallback:** mypy/bandit/pip-audit/pytest-cov are all installable via uv at pinned versions; not blocking.

## Sources

### Primary (HIGH confidence)
- `uv pip compile --help` (uv 0.11.18, run this session) — confirmed `--generate-hashes`, `--python-version`, `--python-platform`, `--universal`, `--output-file`, `--no-strip-extras/markers`.
- Installed-tool checks this session — `uv 0.11.18`, `ruff 0.15.15` confirmed locally.
- `git show --stat` / `git grep` (this session) — BASE-02 revert audit, confirmed clean.
- `core/middleware.py`, `app.py`, `routes/*` greps (this session) — auth-via-middleware model, 41 routers, `setup_*_routes` factory pattern, conftest stub strategy.
- PyPI registry (this session): ruff 0.15.15, mypy 2.1.0, bandit 1.9.4 (2026-02-25), pip-audit 2.10.0 (2025-12-01).
- `https://github.com/astral-sh/setup-uv` — setup-uv at v8.x; `version` + `enable-cache` inputs.
- `.planning/research/PITFALLS.md` (first-party) — Pitfalls 8/9/10/11 ground the ruff/mypy/lock/coverage guidance.
- `.planning/codebase/{CONCERNS,STACK,CONVENTIONS}.md`, `CONTEXT.md`, `ROADMAP.md` (first-party).

### Secondary (MEDIUM confidence)
- `https://docs.astral.sh/uv/pip/compile/` — `.in`→`.lock` model, multiple input files (hash specifics confirmed via `--help` instead).
- `https://mypy.readthedocs.io/en/stable/changelog.html` (+ search) — mypy 2.0/2.1 release line.

### Tertiary (LOW confidence)
- None requiring validation — all load-bearing claims verified against tools or first-party docs.

## Metadata

**Confidence breakdown:**
- Standard stack / versions: HIGH — verified against PyPI + installed binaries this session.
- ruff/mypy config shape: HIGH — grounded in pinned tool behavior + locked decisions + Pitfalls.
- Lockfile recipe: HIGH on commands (verified `--help`); MEDIUM on whether the unified ML resolve converges (A1 — must run to know).
- CI workflow: HIGH — setup-uv@v8 + standard job shape; SC#1 proof is a documented manual step.
- Scorecard: MEDIUM-HIGH — measurement techniques are standard tool outputs; exact JSON schema is a planning-finalized default (D-14).
- BASE-02: HIGH — reverts verified clean this session.

**Research date:** 2026-06-03
**Valid until:** 2026-07-03 (tools are fast-moving — re-verify ruff/mypy/uv versions if planning slips a month; pinning insulates CI regardless)
