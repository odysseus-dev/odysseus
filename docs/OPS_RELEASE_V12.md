# Ops Release v12 - TTL Urgency Sort/Highlight, Audit Risk-Context Filter & Admin Ops Run-ID Search

- Supervised Approval Queue (`static/js/tasks.js`):
  - Visual urgency highlight (`data-approval-expiring="true"`, amber border, and `Expiring soon · Xs left` badge) for pending approvals within 60s of expiry
  - Pending queue sort selector (`#tasks-approvals-sort`: `Queue order` / `Expiring soonest`) ordering visible approvals by ascending `expires_at`
  - Recent Decisions risk-context filter (`#tasks-approval-history-risk`: `All contexts`, `Safe only`, `External untrusted`) composable with tool, decision status, and ID/digest search, cleared via `Reset Filters`
- Admin Ops Observability Console (`static/js/admin.js`):
  - Direct `run_id` substring search (`#admin-ops-run-search`) composable with `#admin-ops-run-status-filter` and `#admin-ops-unsettled-only`
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev12` and `admin.js?v=20261113opsreleasev12` (`static/app.js`, `static/sw.js`)