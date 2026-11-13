# Ops Release v11 - Pending Queue Preview Search, Keyboard Batch Selection & Run-Status Filter

- Supervised Approval Queue (`static/js/tasks.js`):
  - Composable pending queue preview/digest search (`#tasks-approvals-search`) alongside `#tasks-approvals-tool-filter` and `#tasks-approvals-risk-filter`, plus one-click `Clear Filters` (`#tasks-approvals-clear-filters`)
  - Scoped keyboard batch selection shortcuts (`Alt+A` to toggle `Select All` on the active filtered view; `Alt+S` to `Select Safe Only`), guarded against firing inside editable inputs
- Admin Ops Observability Console (`static/js/admin.js`):
  - Effect-run status filter (`#admin-ops-run-status-filter`: `All runs`, `Needs attention`, `Settled only`) paired with `#admin-ops-unsettled-only`
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev11` and `admin.js?v=20261113opsreleasev11` (`static/app.js`, `static/sw.js`)