# Ops Release v8 - Audit Decision Detail Drawer, Copy ID & JSON Export Parity

- Recent Decisions Audit Strip (`static/js/tasks.js`):
  - Click-to-expand decision audit drawer (`#tasks-approval-history-detail`) displaying full `approval_id`, `tool_name`, `decision`, `digest`, `resolved_at`, and `external_untrusted_context_seen` status
  - One-click `Copy ID` (`#tasks-approval-history-copy-id`) clipboard helper with toast notification
  - `Export JSON` (`#tasks-approval-history-export-json`) button wired to `/api/tasks/approvals/history/export?format=json&limit=200` with active `decision` and `tool_name` filters preserved
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev8` (`static/app.js`, `static/sw.js`)