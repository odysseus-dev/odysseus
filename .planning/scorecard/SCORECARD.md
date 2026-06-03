# Engineering Modernization Scorecard

> Rendered from `baseline.json` — **do not edit by hand**. Regenerate with `python scripts/scorecard.py --write` (D-10: JSON is the source of truth).

- Generated: `2026-06-03T18:15:12Z`
- Git SHA: `f90dff84ae4157f571d63aa4d06cc65ba8bbee10`
- Schema version: `1`

## Tool versions

| Tool | Version |
|------|---------|
| ruff | `ruff 0.15.15` |
| mypy | `mypy 2.1.0 (compiled: yes)` |
| bandit | `bandit 1.9.4` |
| pip_audit | `pip-audit 2.10.0` |
| coverage | `Coverage.py, version 7.14.1 with C extension` |
| python | `3.12.13` |

## Headline metrics

| Metric | Value | Ratchet |
|--------|-------|---------|
| Max module LOC | 5174 | <= 5174 |
| Typed % (overall) | 0.4962 | >= 0.4962 |
| Ruff findings | 0 | <= 0 |
| Coverage % (overall) | 45.36 | >= 45.36 |
| Security high/critical | 0 | <= 0 |

## Largest modules (top 15)

| Module | LOC |
|--------|-----|
| `src/tool_implementations.py` | 5174 |
| `routes/email_routes.py` | 3943 |
| `routes/cookbook_routes.py` | 2821 |
| `src/agent_loop.py` | 2778 |
| `src/task_scheduler.py` | 2751 |
| `src/builtin_actions.py` | 2717 |
| `core/database.py` | 2317 |
| `src/ai_interaction.py` | 2141 |
| `routes/model_routes.py` | 2133 |
| `routes/gallery_routes.py` | 2100 |
| `src/tool_schemas.py` | 2009 |
| `routes/skills_routes.py` | 2005 |
| `routes/document_routes.py` | 1940 |
| `src/visual_report.py` | 1908 |
| `mcp_servers/email_server.py` | 1898 |

## Ruff findings by code

_None — clean ruff baseline._

## Security findings

Triage rationale lives in [`security-triage.md`](./security-triage.md).

_None recorded._

## Perf (guardrail, not a gate)

- Cold `import app` mean: `1.461` s over 5 runs (Python 3.12.13)
- Key-path `/api/health` latency: `4.03` ms

## Authenticated endpoints

- Total routes: 420 (406 authenticated, 14 public/exempt)
- This enumeration is the input for Phase 2 COV-03 / Phase 5 SEC-01 and MUST stay in sync with app.py's inline allow-list.

