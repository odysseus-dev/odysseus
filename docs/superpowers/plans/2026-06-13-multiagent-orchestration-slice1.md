# Multiagent Orchestration Slice-1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved multiagent spec
(`docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md`):
`spawn_agent` tool, subagent orchestrator with depth/parallel/budget bounds,
persona/agent profiles, derived `agent:{human}/{name}` owner identity,
multi-owner memory read, hybrid session persistence. Unblocks business-platform
Plan 3.

**Code seams (verified against the tree):**
- Tool wiring: tag in `TOOL_TAGS` (`src/agent_tools/__init__.py`), prompt
  section in `TOOL_SECTIONS` (`src/agent_loop.py:291`), execution branch in
  `execute_tool_block` (`src/tool_execution.py`), schema in
  `FUNCTION_TOOL_SCHEMAS` (`src/agent_tools/tool_schemas.py`).
- Nested run: drain `stream_agent_loop(...)` (async SSE generator) like
  `src/agent_runs.py:_drain` / `_run_verifier_subagent` precedent; collect
  deltas → final text. Subagent restriction via `relevant_tools` /
  `disabled_tools` / `owner=agent_id` parameters that already exist.
- Depth tracking: explicit `_depth` plumbed through the orchestrator (module
  contextvar), `spawn_agent` disabled in nested loops at `depth >= cap`.
- Profiles: `services/agents/profile.py` reads `data/personas/<n>/SOUL.md`,
  `data/agents/<n>/agent.json` — the exact formats Plan-2's profile compiler
  emits.
- Memory: `src/memory.py` gets `load_multi(owners: list)` →
  `owner IN (...) OR owner IS NULL` read; writes already take owner.
- Sessions: `persist=true` → child Session via
  `core/session_manager.ensure_task_session` pattern with
  `metadata.parent_session_id`, `metadata.human_owner`,
  `metadata.kind="subagent"`, `owner=agent_id`.
- Settings: `src/settings.py` defaults `agent_max_depth=2`,
  `agent_max_parallel=2`.
- Auth: login path (`routes/auth_routes.py` / auth manager) rejects
  `agent:`-prefixed usernames (internal-only owners).

**Safety invariants (spec):** persona text + skills injected as untrusted
user-role content; subagent results = untrusted tool output; human owner
inherited at spawn, never elevated; memory reads limited to
`{agent_id, human_id, NULL}`; depth cap strips `spawn_agent`; parallel cap
queues excess; malformed args → tool-result error, no crash.

---

### Task 1: Profiles — `services/agents/profile.py`
- [ ] `load_persona(name)`, `load_agent(name)`, `resolve_binding(entry, human)`
  (stored agent ref OR inline persona+tools), `derive_owner(human, name)` →
  `agent:{human or "local"}/{name}`. Tests: golden artifacts from the Plan-2
  compiler compile→load round-trip; missing persona/agent errors.
- [ ] Commit.

### Task 2: Memory multi-owner read + auth guard + settings defaults
- [ ] `Memory.load_multi(owners)` (single-owner behavior unchanged); login
  rejects `agent:`-prefixed usernames; settings defaults. Tests: multi-owner
  read returns own+human+shared, never another human's; login 403; defaults
  present.
- [ ] Commit.

### Task 3: Child sessions
- [ ] Subagent child session: `owner=agent_id`,
  `metadata.{parent_session_id,human_owner,kind}`. Test: created+linked,
  `_verify_session_owner` still gates.
- [ ] Commit.

### Task 4: Orchestrator — `src/subagent_orchestrator.py`
- [ ] `spawn(entries, mode, persist, ctx)` with: binding resolution, tool
  scoping intersection, depth cap, parallel cap (asyncio.Semaphore),
  sequential + parallel dispatch, structured per-entry results + join,
  failure isolation (sibling unaffected), refusal results on
  depth/budget exhaustion. Loop-runner injected (callable) so unit tests mock
  the LLM entirely. SOUL.md injected as untrusted user message in nested
  message list.
- [ ] Tests per spec §Testing list.
- [ ] Commit.

### Task 5: `spawn_agent` tool wiring
- [ ] TOOL_TAGS + TOOL_SECTIONS + FUNCTION_TOOL_SCHEMAS + execute_tool_block
  branch → orchestrator; `spawn_agent` in nested `disabled_tools` at max
  depth. Test: tool call path end-to-end with mocked runner; malformed args
  → error result.
- [ ] Commit.

### Task 6: Regression + wrap-up
- [ ] Full suite green; `graphify update`; `codex review --base <start sha>`;
  fix findings; commit.

## Out of scope
UI; #724 full profile subsystem; memory promotion; per-agent prefs; remote
runtimes. Business-platform Plan 3 starts after this lands.
