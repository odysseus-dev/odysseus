# Ops Release v2 (feat/ops-release-v2)

## Overview
Builds on feat/ops-release-v1 with per-run Wave 4 effect-log drill-down inspection and live Tasks approval queue polling with per-item expiry countdowns.

## Changes

### M1: Per-Run Effect Log Drill-Down (Admin-Only)
- GET /api/diagnostics/effects/{run_id}: Guarded by require_admin(request) and strict 32-hex run_id validation (400 on invalid run_id, 404 if missing). Returns ordered EffectLog records alongside reconciliation counters (unsettled_claims, corrupt_lines).

### M2: Live Approval Queue Polling & Expiry Countdown
- static/js/tasks.js (asset version tasks.js?v=20261113opsreleasev2 in static/app.js and static/sw.js): Polls GET /api/tasks/approvals/pending every 10s while visible, updates [data-approval-ttl] countdown badges every 1s, prunes expired approvals client-side, and adds a manual #tasks-approvals-refresh button.

### M3: Verification & Asset Cache-Busting
- tests/test_ops_release_v2_approvals_ui_js.py: Node VM runtime test verifying _formatApprovalTtl, _pruneExpiredApprovals, live countdown ticks, and app.js/sw.js asset version parity.
- tests/test_ops_release_v2_e2e.py: End-to-end verification of /api/diagnostics/effects -> /api/diagnostics/effects/{run_id} reconciliation drill-down and /api/tasks/approvals/pending + /resolve.

## Rollback
To fall back to feat/ops-release-v1: git checkout feat/ops-release-v1
