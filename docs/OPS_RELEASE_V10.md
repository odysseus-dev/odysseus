# Ops Release v10 - Risk-Context Filter, Audit ID/Digest Search, Polling Pause & Unsettled Jump

- Supervised Approval Queue (`static/js/tasks.js`):
  - Composable pending queue context-risk filter (`#tasks-approvals-risk-filter`: `All contexts`, `Safe only`, `External untrusted`) alongside `#tasks-approvals-tool-filter`, scoping `Select All`, `Select Safe Only`, and bulk resolution actions to the active intersection
  - Polling pause/resume toggle (`#tasks-approvals-pause-toggle`: `Pause Poll` / `Resume Poll`) to freeze background 10s queue refreshes while keeping live 1s TTL countdowns running
  - Direct `approval_id` / `digest` search input (`#tasks-approval-history-search`) in Recent Decisions, cleared cleanly via `Reset Filters`
- Admin Ops Observability Console (`static/js/admin.js`):
  - Surfaces `effects.unsettled_runs` alongside `Effect Runs` with a one-click `Triage Unsettled` (`#admin-ops-jump-unsettled`) jump that activates `#admin-ops-unsettled-only`
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev10` and `admin.js?v=20261113opsreleasev10` (`static/app.js`, `static/sw.js`)