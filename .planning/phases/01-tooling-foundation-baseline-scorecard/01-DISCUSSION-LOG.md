# Phase 1: Tooling Foundation & Baseline Scorecard - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-03
**Phase:** 1-Tooling Foundation & Baseline Scorecard
**Areas discussed:** CI gating posture, Lockfile strategy, Scorecard format, Lint/type baseline

---

## CI Gating Posture

### Enforcement (day 1)
| Option | Description | Selected |
|--------|-------------|----------|
| Hard-block on clean baseline | Reach zero findings first, then every gate hard-blocks; deliberate violation must fail the PR check | ✓ |
| Warn-then-enforce ramp | Non-blocking annotations first, flip to blocking once clean | |
| Hybrid: block new, tolerate legacy | Block most gates day 1; mypy baseline-tolerant (grandfather legacy, block new) | |

### Workflow structure
| Option | Description | Selected |
|--------|-------------|----------|
| Parallel jobs | Separate jobs (lint, format, mypy, pytest, bandit, pip-audit) in parallel | ✓ |
| Single sequential job | One job, checks in sequence | |

### Triggers
| Option | Description | Selected |
|--------|-------------|----------|
| PR + push to main | pull_request and push to main | ✓ |
| PR only | pull_request only | |

### CI install
| Option | Description | Selected |
|--------|-------------|----------|
| uv from lockfile | setup-uv action, install against committed lockfile | ✓ |
| pip from lockfile | plain pip install -r requirements.lock | |

**User's choice:** Hard-block on clean baseline · parallel jobs · PR + push to main · uv from lockfile.

---

## Lockfile Strategy

### Structure
| Option | Description | Selected |
|--------|-------------|----------|
| Single unified lockfile | One requirements.lock resolving the full set together | ✓ |
| Split core + ML group | Separate requirements-ml for chromadb+fastembed+onnxruntime | |

### Source
| Option | Description | Selected |
|--------|-------------|----------|
| New requirements.in (top-level only) | Author direct deps, compile to pinned lock | ✓ |
| Pin existing requirements.txt as-is | Freeze current file without restructuring | |

### Optional deps (AGPL PyMuPDF)
| Option | Description | Selected |
|--------|-------------|----------|
| Separate optional lock | Own pinned lock, preserves MIT/AGPL boundary | ✓ |
| Fold into main lock | Merge into single lock | |
| Leave unpinned for now | Defer optional-dep pinning | |

### Hashes / environment
| Option | Description | Selected |
|--------|-------------|----------|
| Hashes, generated in python:3.12-slim | --generate-hashes inside CI base image | ✓ |
| No hashes, local generation | Compile locally without hashes | |

**User's choice:** Single unified core lock from a new requirements.in · separate optional lock for the AGPL group · --generate-hashes inside python:3.12-slim.
**Notes:** Split-ML group retained only as a fallback if unified resolution fails to converge.

---

## Scorecard Format

### Format
| Option | Description | Selected |
|--------|-------------|----------|
| JSON + generated markdown view | JSON source of truth, rendered markdown summary | ✓ |
| JSON only | Single JSON artifact | |
| Markdown table only | Human table only | |

### Thresholds
| Option | Description | Selected |
|--------|-------------|----------|
| Relative ratchet (no-regression) | Threshold = baseline; CI fails on regression | ✓ |
| Absolute targets | Fixed end-state goals | |
| Hybrid: ratchet + a few absolutes | Ratchet most, absolutes where roadmap implies | |

### Generation
| Option | Description | Selected |
|--------|-------------|----------|
| Scripted + re-runnable | Committed script regenerates metrics on demand and in CI | ✓ |
| Manual one-time capture | Measure once by hand | |

### Location
| Option | Description | Selected |
|--------|-------------|----------|
| .planning/scorecard/ | With planning artifacts, outside app tree | ✓ |
| Repo root or docs/ | In the shipped tree | |

**User's choice:** JSON source-of-truth + rendered markdown · relative no-regression ratchet · scripted/re-runnable generator · .planning/scorecard/.

---

## Lint / Type Baseline

### Ruff rule families
| Option | Description | Selected |
|--------|-------------|----------|
| Core + safe modernizers | E, F, I, W, UP, B | ✓ |
| Minimal | E, F, I only | |
| Broad curated | Core + SIM, C4, PIE, RUF, PTH | |

### Format rollout
| Option | Description | Selected |
|--------|-------------|----------|
| Whole-repo format, standalone commit | One isolated commit + .git-blame-ignore-revs, then CI format-check | ✓ |
| Format changed files only | Enforce only on touched files | |

### Mypy global baseline
| Option | Description | Selected |
|--------|-------------|----------|
| Lenient global + ignore_missing_imports | No disallow-untyped; ignore missing imports; check_untyped_defs off | ✓ |
| Lenient but check_untyped_defs on | Same leniency, type-check untyped bodies | |

### Auto-fix commits
| Option | Description | Selected |
|--------|-------------|----------|
| Separate atomic commits | config / --fix / format as three commits | ✓ |
| One combined commit | All in one | |

**User's choice:** ruff E/F/I/W/UP/B · whole-repo format in a standalone commit (+ blame-ignore) · mypy lenient global + ignore_missing_imports · atomic config/fix/format commits.

---

## Claude's Discretion

Deferred to planning with stated defaults: BASE-02 dead-code audit approach (reverts `67b63e9`/`1f6c5ac`); bandit/pip-audit suppression conventions; exact scorecard JSON schema + perf-benchmark harness; mypy per-module override granularity; ruff version pin value; E501/line-length disabled to match the no-hard-column convention.

## Deferred Ideas

- TOOL-05 (`datetime.utcnow()` replacement) — Phase 3.
- FE-02 (eslint + prettier for `static/`) — v2.
- LOG-01 (structured logging), EXC-01 (broad-except narrowing) — v2.
- Broadening ruff rule set beyond the core families — via later-phase ratchet, not front-loaded.
