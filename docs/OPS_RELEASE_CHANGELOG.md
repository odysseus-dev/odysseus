# OdyAI Operational Hardening Changelog (Ops Releases v1–v12)

**Release Baseline:** `dev` @ `0897a137` (`fork/dev`)  
**Asset Cache-Buster:** `?v=20261113opsreleasev12`  
**Verification Gate:** 25 Python Ops/Diagnostics E2E & unit tests + 9 Node UI test suites (`v4`–`v12`) + automated PowerShell launch smoke verification (`scripts/verify_odyai_launch.ps1`)

---

## Summary Table

| Release | Focus Area | Key Capabilities Shipped |
| :--- | :--- | :--- |
| **v1** | Runtime Health & Effect Audit | Service health probes, aggregated `/api/diagnostics/ops-summary`, and persistent append-only effect logging (`src/agent_runtime/effect_log.py`). |
| **v2** | Run Drill-Down & Live Queue | Per-run effect inspection (`/api/diagnostics/effects/{run_id}`) and live Supervised Approval Queue polling with TTL countdown timers in Tasks UI. |
| **v3** | Unsettled Triage & Reconcile | `unsettled_only` filtering and auditable operator reconciliation (`INTERRUPTED` / `CANCELLED`) with reason recording in Admin Ops. |
| **v4** | Bulk Approval Resolution | Batch approve/reject endpoint (`/api/tasks/approvals/bulk`) with owner isolation, atomic validation guards, and decision history tracking. |
| **v5** | External Guard & CSV Export | Fail-closed bulk guard against untrusted external-side-effect actions plus filtered CSV decision history export. |
| **v6** | Selective Batch Checkboxes | Granular per-item batch selection checkboxes, tool-name filtering on pending approvals, and synchronized asset cache-busters. |
| **v7** | Safe-Only Batch Selection | One-click **Select All** / **Select Safe Only** triage controls and live approval/rejection decision counters. |
| **v8** | Audit Detail Drawer & JSON | Expandable audit detail drawer for inspecting full tool arguments/metadata, one-click **Copy ID**, and JSON audit export. |
| **v9** | Keyboard Triage & Cross-Links | Keyboard drawer navigation, tool filter refinements, and direct cross-links from approval items into Admin Ops run diagnostics. |
| **v10** | Risk-Context & Polling Control | Risk-context filtering, live queue polling pause/resume toggle, audit search bar, and deep-link jumping into Admin Ops runs. |
| **v11** | Preview Search & Hotkeys | Pending queue argument preview search, one-click **Clear Filters**, keyboard shortcuts (`Alt+A` approve / `Alt+S` skip), and Admin Ops run filter. |
| **v12** | TTL Urgency & Run Search | Automatic urgency sorting and `Expiring soon · Xs left` visual highlighting for expiring approvals, audit risk filters, and Admin Ops run-ID search. |

---

## Detailed Release Notes

### Phase 1: Observability & Effect Accountability (v1–v3)
* **Ops Release v1 — System Health & Effect Logging:** Introduced `src/service_health.py`, admin-gated `/api/diagnostics/ops-summary` and `/api/diagnostics/services`, and structured JSONL effect logging (`src/agent_runtime/effect_log.py`) to record tool execution lifecycles.
* **Ops Release v2 — Per-Run Drill-Down & Live Approval Polling:** Added `/api/diagnostics/effects/{run_id}` for inspecting individual execution traces and built real-time approval polling with live TTL countdowns into `static/js/tasks.js`.
* **Ops Release v3 — Unsettled Triage & Operator Reconciliation:** Added `?unsettled_only=true` filtering to surface incomplete or orphaned runs and implemented auditable reconciliation (`INTERRUPTED` / `CANCELLED` with operator attribution and notes) in `static/js/admin.js`.

### Phase 2: High-Throughput Supervised Approvals & Audit Export (v4–v8)
* **Ops Release v4 — Bulk Approvals & Decision History:** Added owner-isolated batch approval resolution in `src/tool_approvals.py` and `routes/task/task_routes.py`, paired with an operator decision history log.
* **Ops Release v5 — External Side-Effect Bulk Guard & CSV Export:** Enforced a fail-closed guard blocking bulk approval of untrusted external side-effect tools (requiring explicit individual review) and added filtered CSV export for audit trails.
* **Ops Release v6 — Selective Batch Controls & Tool Filtering:** Replaced all-or-nothing batch actions with individual selection checkboxes, pending queue filtering by tool name, and synchronized service-worker/app asset cache-busting (`static/app.js`, `static/sw.js`).
* **Ops Release v7 — Safe-Only Selection & History Counters:** Added **Select All** and **Select Safe Only** batch helpers alongside real-time approved/rejected/expired decision summary counters.
* **Ops Release v8 — Expandable Audit Drawer & JSON Export:** Added expandable inspection drawers on decision history entries to view full payloads and context, one-click **Copy ID**, and structured JSON export.

### Phase 3: Rapid Operator Triage & Cross-Console Diagnostics (v9–v12)
* **Ops Release v9 — Keyboard Drawer & Admin Ops Cross-Linking:** Added keyboard navigation for audit drawers and direct deep links from approval records to their corresponding run diagnostics in Admin Ops.
* **Ops Release v10 — Risk-Context Filtering & Polling Pause/Resume:** Added risk-tier filtering, full-text audit history search, and a polling pause/resume toggle so queues do not shift mid-inspection.
* **Ops Release v11 — Argument Preview Search & Triage Hotkeys:** Added instant search across pending tool argument previews, one-click **Clear Filters**, `Alt+A` / `Alt+S` keyboard shortcuts for rapid queue triage, and run-level filtering in Admin Ops.
* **Ops Release v12 — TTL Urgency Prioritization & Run-ID Search:** Added automatic TTL urgency sorting and `Expiring soon · Xs left` badge highlighting for time-sensitive approvals, audit history risk filtering, Admin Ops run-ID search, and unified `v20261113opsreleasev12` cache-busters.

---

## Security & Deployment Verification
* **Fail-Closed Access Control:** All `/api/diagnostics/*` endpoints (`ops-summary`, `effects`, `services`, `logs`) enforce admin authentication and return `401`/`403` to unauthenticated or non-admin callers.
* **Automated Launch Gate (`scripts/verify_odyai_launch.ps1`):** Validates branch/commit parity (`0897a137`), cache-buster synchronization across `app.js` and `sw.js`, `/api/version` health, and unauthenticated/authenticated endpoint responses prior to traffic cutover.
