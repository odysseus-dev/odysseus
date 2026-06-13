# Business Platform Mission Loop (Slice-1, Plan 3 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Big Boss mission loop per spec
`docs/superpowers/specs/2026-06-13-business-platform-slice1-design.md` §6,
minimal slice: **plan → bounded dispatch (signed envelopes over the hub) →
timeline status → report**, wired to the manager approval surface (§7). No
privileged side channel — missions ride the same hub bus as every other
message.

**Builds on (already shipped):** envelope v1 + Ed25519 signing (`envelope.py`),
hub ingest/gating/audit (`hub.py`), registry + service keys (`registry.py`),
approval queue (`approval.py`), profile compiler (`profile_compiler.py`),
multiagent orchestrator (`subagent_orchestrator.py`).

**Decisions:**
- **Big Boss is a registered company** (`bigboss`) with its own Ed25519
  service identity, so its mission envelopes verify like any other sender
  (`ensure_big_boss(owner)` creates it idempotently, owner = the human).
- **Decomposition is provided, not invented, in slice-1.** A mission is
  created with a goal + an explicit task list `[{company, intent, task}]`.
  The "plan visible/steerable" requirement = the stored `MissionTask` rows,
  editable while `status=planning`. LLM goal→plan decomposition is a thin
  later layer, out of scope here (keeps the loop deterministic + testable).
- **Task terminal status derives from envelope state** (no bespoke channel):
  - ungated intent → ingested & delivered to the company inbox → `dispatched`;
  - gated intent → parked → `blocked` (awaits manager approve/deny);
  - gated `denied` → `failed`; gated `approved` (no reply yet) → `dispatched`;
  - a **reply envelope** (company → bigboss, same `conversation_id`,
    `status finished|error`) → `completed` / `failed`. Replies are how a
    company runtime reports results; tests inject them (real runtime = later).
- **Conversation id** ties a task to its envelope + replies:
  `mission:{mission_id}:task:{task_id}`. Delegation chain in every envelope:
  `from_subject = human:{owner}`, `from_company = bigboss`.
- **Approval surface = the existing** `/api/platform/approvals*` endpoints
  (list/approve/deny) plus the new mission timeline as the owner view. Push
  notifications are slice-2.

**Tech:** reuse everything; new code is the mission data model + service +
3 routes.

---

### Task 1: DB model — Mission, MissionTask
**Files:** `core/database.py` (append before `init_db()`); test
`tests/test_platform_mission_db.py`.
- [ ] `Mission(id, goal, owner, status[planning|running|completed|failed],
  report, created_at)`; `MissionTask(id, mission_id FK, seq, target_company,
  intent, task_text, conversation_id, envelope_message_id, status[pending|
  dispatched|blocked|completed|failed], result)`. Self-contained round-trip
  test.
- [ ] Commit.

### Task 2: `ensure_big_boss` + mission service create/plan
**Files:** `services/business_platform/mission.py`; test
`tests/test_platform_mission.py`.
- [ ] `ensure_big_boss(owner)` → idempotent registry company `bigboss`
  (manager = the human). `create_mission(goal, owner, tasks)` → Mission
  (status=planning) + MissionTask rows (status=pending, conversation_id set).
  `update_plan(mission_id, tasks)` allowed only while planning (steerable).
- [ ] Tests: create, plan edit gated to planning state.
- [ ] Commit.

### Task 3: dispatch + refresh + report
- [ ] `dispatch_mission(mission_id)`: for each pending task build a signed
  envelope (bigboss→target, intent, conversation_id, from_subject=
  human:{owner}) and `hub.ingest`; record envelope_message_id; task →
  `blocked` if hub parked it (gated), else `dispatched`; mission → running.
  Bounded/sequential.
- [ ] `refresh_mission(mission_id)`: recompute each non-terminal task from
  envelope + GatedIntent + reply envelopes; when all terminal, synthesize
  `report` and set mission completed/failed.
- [ ] Tests: ungated→dispatched→(reply)→completed; gated→blocked→approve→
  dispatched→reply→completed; gated→deny→failed; report synthesis; injection
  payload in a gated task never auto-runs (reuse §5 guarantee).
- [ ] Commit.

### Task 4: HTTP surface
**Files:** `routes/platform_routes.py`; test `tests/test_platform_mission_routes.py`.
- [ ] `POST /api/platform/missions` (admin) {goal, tasks} → create+dispatch;
  `GET /api/platform/missions/{id}` → mission + task timeline;
  `POST /api/platform/missions/{id}/refresh` → refresh+maybe report.
- [ ] Tests via TestClient (stub auth_manager, like Plan-1 routes test).
- [ ] Commit.

### Task 5: Regression + wrap-up
- [ ] Platform + whole-repo suites green; `graphify update`;
  `codex review --base <plan-3 start sha>`; fix findings; commit.

## Out of scope (slice-2+)
LLM goal→plan decomposition; real company-runtime execution of inbox
envelopes (multiagent integration); end-customer web chat + mobile-app
upsell; push notifications; mission concurrency beyond bounded sequential;
artifact studio.
