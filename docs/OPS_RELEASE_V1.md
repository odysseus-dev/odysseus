# Ops Release v1 (`feat/ops-release-v1`)

## Overview
Adds supervised human-in-the-loop approval visibility, read-only Wave 4 effect-log auditing, and a unified admin operational runtime summary without modifying database schemas or bypassing existing security boundaries.

## Endpoints

### Supervised Approval Queue (Owner-Scoped)
- `GET /api/tasks/approvals/pending`
  - Returns non-authoritative display summaries (`approval_id`, `session_id`, `origin_run_id`, `tool_name`, `content_preview`, `digest`, `effects`, `workspace`, `document_id`, `document_version`, `external_untrusted_context_seen`, `created_at`, `expires_at`) strictly filtered by `_normalized_owner(user)`.
- `POST /api/tasks/approvals/{approval_id}/resolve`
  - Body: `{"decision": "approve_task" | "deny"}`
  - Enforces `require_user_approval_request(request)` and owner verification, consuming the approval with `allow_continuation=False` so the gate re-arms immediately after the sealed action.

### Diagnostics & Operational Health (Admin-Only)
- `GET /api/diagnostics/effects?limit=50`
  - Guarded by `require_admin(request)`. Summarizes recent durable `EffectLog` files (`claims`, `outcomes`, `observations`, `tools`, `external_claims`, `mutation_attempted`, `corrupt`) strictly read-only.
- `GET /api/diagnostics/ops-summary`
  - Guarded by `require_admin(request)`. Combines `collect_service_health()` with read-only `ScheduledTask` status counts, `tool_approval_store` pending queue depth, and recent `EffectLog` audit counters.

## Security Invariants
1. **Owner Isolation:** Pending approvals are filtered by normalized owner; cross-owner `GET` returns `count: 0` and cross-owner `POST /resolve` returns `404`.
2. **Fail-Closed Single-Action Consumption:** Resolving via the tasks queue forces `allow_continuation=False`.
3. **No Secret Leakage:** Service health probes strip URL userinfo/query/fragments via `_safe_url()` and map exceptions to controlled categories.

## Verification
```powershell
.venv\Scripts\python.exe -m pytest tests/test_supervised_approval_queue.py tests/test_diagnostics_effects_route.py tests/test_diagnostics_ops_summary.py tests/test_ops_release_v1_e2e.py tests/test_tool_approvals.py tests/test_tool_approval_single_action_scope.py tests/test_service_health_collect.py tests/test_diagnostics_service_route.py
node --check static/js/tasks.js
```

## Rollback
`dev` remains pinned to `origin/dev` (`3d01007b`). To roll back immediately:
```powershell
git checkout dev
```
