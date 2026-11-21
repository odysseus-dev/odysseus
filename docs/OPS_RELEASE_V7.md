# Ops Release v7 - Batch Selection Ergonomics, Audit Summary Counters & Asset Sync

- Pending Approvals Queue (`static/js/tasks.js`):
  - Master header checkbox (`#tasks-approvals-select-all`) with automatic `indeterminate` state on partial selection
  - One-click `Select Safe Only` (`#tasks-approvals-select-safe`) preset to deselect actions flagged with `external_untrusted_context_seen`
- Recent Decisions Audit Strip (`static/js/tasks.js`):
  - Inline decision summary counters (`Approved: X · Denied: Y`)
  - One-click `Reset Filters` (`#tasks-approval-history-reset`) control when `decision` or `tool_name` filters are active
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev7` (`static/app.js`, `static/sw.js`)