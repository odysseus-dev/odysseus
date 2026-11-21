# Ops Release v6 - Selective Batch Approvals, Filtered Audit Export & Asset Sync

- Bulk approval resolution endpoint: `POST /api/tasks/approvals/resolve-bulk` (`decision`: `approve` | `approve_task` | `deny`, optional `skip_external_untrusted: true`)
- Owner-scoped decision audit history: `GET /api/tasks/approvals/history?limit=8&decision=approve|deny&tool_name=<tool>`
- Owner-scoped CSV/JSON audit export: `GET /api/tasks/approvals/history/export?format=csv|json&limit=200&decision=...&tool_name=...`
- Tasks Approval Queue UI (`static/js/tasks.js`):
  - Per-item batch selection checkboxes (`Approve Selected (N)` / `Deny Selected (N)`)
  - Confirmation guard when bulk-approving actions flagged with external untrusted context
  - Decision filter pills (`All` / `Approved` / `Denied`), tool-name dropdown filter, and one-click `Export CSV`
- Service worker & ES module cache-busting synchronized at `tasks.js?v=20261113opsreleasev6` (`static/app.js`, `static/sw.js`)