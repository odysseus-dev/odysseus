# Ops Release v9 - Pending Queue Tool Filter, Keyboard Audit Drawer & Admin Ops Cross-Link

- Supervised Approval Queue (`static/js/tasks.js`):
  - Inline pending queue tool filter dropdown (`#tasks-approvals-tool-filter`) when 2+ distinct tools are queued, scoping `Select All`, `Select Safe Only`, and bulk resolution actions to the active filtered view
  - Keyboard-accessible audit detail drawer (`Enter` / `Space` on `.task-approval-history-pill` to toggle, `Escape` to dismiss `#tasks-approval-history-detail`)
- Admin Ops Observability Console (`static/js/admin.js`):
  - Displays `external_untrusted_count` breakdown alongside `pending_total` in `OPS RUNTIME` with a one-click `Open Queue` (`#admin-ops-open-approvals`) navigation button to the Tasks approval queue
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev9` and `admin.js?v=20261113opsreleasev9` (`static/app.js`, `static/sw.js`)