# Odysseus Multi-Agent Orchestration — Slice-1 Design

**Date:** 2026-06-12
**Status:** Design (approved for spec review)
**Scope:** Server-side, `tools/odysseus`. Slice-1 of the "Odysseus adaptation for
multi-agentic work" subproject. UI is explicitly out of scope.

## Context

Odysseus (github.com/pewdiepie-archdaemon/odysseus) already ships the single-agent
half of an agentic workspace: `src/agent_loop.py` (plan → call tool → observe →
repeat), a `SkillsManager` (SKILL.md registry, Jaccard relevance match), persona /
skill prompt injection, MCP servers, ChromaDB-backed memory, and two integration
surfaces (`integrations/claude`, `integrations/codex`). It has **no subagent
spawning** — one agent per run.

Upstream Discussion #724 ("Agent Profiles with Persistent Identity — SOUL.md +
Isolated Memory") proposes the *identity* half (persistent `SOUL.md`, isolated
ChromaDB-namespace memory per profile) but explicitly lacks a coordinator /
subagent orchestration pattern, and cites Hermes Agent as prior art. The ROADMAP
flags two relevant concerns: agent mode is "too heavy for smaller local models,"
and a "skill/tool prompt-injection audit" is outstanding.

This slice supplies the missing orchestration: a coordinator that spawns scoped
subagents over the existing `agent_loop`, reusing skills, MCP, and memory.

## Decisions (locked during brainstorming)

- **Approach:** subagent tool over the existing `agent_loop` (vs. external plugin,
  vs. full Agent-Profiles subsystem). Smallest slice, server-side, reuses
  everything, forward-compatible with #724.
- **Coordination:** hybrid — the coordinator chooses sequential or bounded-parallel
  dispatch per call (`mode: auto|sequential|parallel`).
- **Persona vs. agent:** separate concepts. A **persona** is a reusable identity
  (`SOUL.md`, no tools). An **agent** is a binding that references a persona and
  adds tools + optional model. One persona is reusable across many agents.
- **Agent = owner identity (not a separate isolation mechanism).** An agent is
  modeled as an internal **owner identity** (a namespaced username string), reusing
  Odysseus's existing per-owner interface (`SessionManager`, `owner_filter`,
  owner-keyed memory, global prefs). This replaces an invented per-agent ChromaDB
  "memory namespace" — the native `owner` mechanism already provides isolation and
  sharing. Agent owner-ids are internal-only: no password, cannot log in, prefixed
  `agent:` so they cannot collide with or impersonate a human username.
- **Memory sharing:** an agent **reads** `owner IN (agent_id, human_id, NULL)` —
  the human's memories + shared (null-owner) + its own — and **writes** to
  `agent_id`, so agent-generated memories stay separable until explicitly promoted.

## Concepts

### Persona (reusable identity)

- Stored: `data/personas/<name>/SOUL.md` (+ optional `meta.json`: `{description}`).
- Pure identity / behavior text. No tools, no memory, no loop. Inert on its own.
- Reusable: many agents may reference the same persona.

### Agent (binding)

- Stored: `data/agents/<name>/agent.json`:
  ```json
  {
    "persona": "researcher-persona",
    "tools": ["web", "documents", "memory"],
    "model": null
  }
  ```
- An agent = persona + tool allowlist + optional model override. Same persona +
  different toolset = a different agent.
- `model: null` → inherit coordinator's model.
- **Owner id is derived at spawn, not stored**: `agent:{human_owner or "local"}/{name}`
  (e.g. `agent:oleg/researcher`). This is the agent's owner identity for sessions
  and memory. The `agent:` prefix keeps it disjoint from human usernames.

## Architecture

```
coordinator (top-level agent_loop, default persona, full tools)
  │  LLM emits spawn_agent tool call
  ▼
subagent_orchestrator
  │  resolve agent(s): persona SOUL.md + tool allowlist + derived owner id
  │  scope tools = intersection(agent.tools, parent-enabled tools)
  │  dispatch: sequential | bounded-parallel (mode=auto picks by task count)
  ▼
nested agent_loop  ×N   (each: own conversation, scoped tools, injected SOUL.md,
  │                       agent owner identity, shared tree budget)
  ▼
join → structured results → returned to coordinator as one tool result
  ▼
coordinator synthesizes / continues
```

### `spawn_agent` tool

Registered in `src/tool_index.py`; implemented in `src/tool_implementations.py`
delegating to `src/subagent_orchestrator.py`.

Arguments (JSON):
```json
{
  "mode": "auto",
  "persist": false,
  "agents": [
    { "agent": "researcher", "task": "Find Q2 churn drivers" },
    { "persona": "summarizer-persona", "tools": ["documents"], "task": "Summarize doc 41", "persist": true }
  ]
}
```
- Each entry references a stored `agent` by name, OR specifies an ad-hoc binding
  inline (`persona` + `tools`).
- `persist` (top-level default, per-entry override) controls session handling —
  see "Session handling".
- Single entry (or `mode=sequential`) → blocking sequential dispatch, result
  returned before the coordinator continues.
- Multiple entries with `mode=parallel` (or `auto` + count > 1) → bounded-parallel
  fan-out, joined.

### Prompt assembly for subagents

Reuse `_build_system_prompt` / `_assemble_prompt` with a new override hook so the
subagent's persona `SOUL.md` is injected at the top of its system prompt. Skill
injection (Jaccard match) continues to work per the subagent's task and scoped
tools.

### Identity & memory (owner model)

Reuse Odysseus's native per-owner mechanism instead of an invented namespace.
Memory rows are owner-keyed; `owner_filter` already yields
`(owner == user) | (owner IS NULL)` (null = shared). The agent's owner id is the
derived `agent:{human}/{name}`.

- **Read**: `owner IN (agent_id, human_id, NULL)` — the human's memories + shared +
  the agent's own. This is the "keep touch on the user's memory" behavior.
- **Write**: `owner = agent_id` — agent-generated memories stay separable from the
  human's store until explicitly promoted (a later slice may add promotion).
- **Prefs**: global `data/settings.json` (not per-owner) — automatically shared by
  humans and agents; no work, no per-agent prefs in slice-1.

This adds a small memory-read helper that accepts a list of owners (today
`memory.load(owner)` takes a single owner); writes already accept an explicit
`owner`.

### Session handling

Odysseus already has a DB-backed `core/session_manager.py` (`SessionManager`):
`create_session`, `add_message`, `get_sessions_for_user`, per-user `owner`
enforcement (`_verify_session_owner`), and `ensure_task_session(...)` which already
materializes sessions for background tasks. It has no parent/child linkage.

Slice-1 is **hybrid**:

- **Ephemeral (default, `persist=false`)** — the subagent's conversation lives only
  in memory for the duration of the nested `agent_loop`. Nothing is written to the
  sessions store. Lighter; no audit trail.
- **Persisted (`persist=true`)** — the orchestrator materializes a **child Session**
  via the `ensure_task_session` pattern:
  - `owner = agent_id` (the derived `agent:{human}/{name}`), so the session belongs
    to the agent identity but stays traceable to the human via the prefix.
  - `metadata.parent_session_id` = coordinator session id (the one new field; the
    missing parent/child link).
  - `metadata.human_owner` = the originating human username (audit / promotion).
  - `metadata.kind = "subagent"` so the sessions panel and cleanup can distinguish
    them; eligible for auto-archive via the existing `cleanup_empty_sessions`.
  - subagent messages appended through `add_message`, giving a full audit trail and
    panel visibility.

The owner model governs memory; the Session governs conversation/history
persistence. Both apply independently of `persist`.

## Safety / bounds (addresses ROADMAP concerns)

- **Recursion depth cap** — `agent_max_depth` (default 2). A subagent may spawn
  only while `depth < cap`; `spawn_agent` is stripped from the toolset at max
  depth. Prevents fork bombs.
- **Shared tree budget** — token + tool-call budget is shared across the whole
  agent tree, reusing the existing agent timeout / max-tool-calls settings. Budget
  exhaustion → `spawn_agent` returns a refusal result, not a crash.
- **Parallel concurrency cap** — `agent_max_parallel` (default 2) protects the
  GPU (RTX 3090 typically near-full with model serves). Excess tasks queue.
- **Prompt-injection** — persona `SOUL.md` and skills are user-editable, therefore
  untrusted. They are injected as a separate user-role message with
  `metadata.trusted=False` (the same treatment `agent_loop` already gives skills
  and documents). Subagent results return to the coordinator as untrusted tool
  output, never as system text. No new external network surface — subagents use the
  same scoped tools.
- **Ownership / principal** — an agent is an actor under a human, never a principal.
  - Agent owner-ids are **internal-only**: created without a password, rejected by
    the login path, prefixed `agent:` so they cannot collide with or impersonate a
    human username.
  - The human owner is **inherited at spawn and never elevated**; a subagent cannot
    choose an arbitrary `human_id`. Memory reads are restricted to
    `owner IN (own agent_id, the inheriting human_id, NULL)` — never another human's
    owner. `_verify_session_owner` continues to gate child sessions.
  - Net: an agent can reach its own + its human's + shared data, and nothing
    belonging to a different human.

## Data flow

1. Coordinator LLM emits a `spawn_agent` tool call.
2. Orchestrator resolves each entry to a concrete binding (persona text + scoped
   tools + derived agent owner id), enforcing depth, scoping, and inherited human
   ownership.
3. Nested `agent_loop`(s) run — sequentially, or bounded-parallel then joined.
4. Each subagent returns a structured result summary.
5. Orchestrator joins results into one tool result returned to the coordinator.
6. Coordinator synthesizes or continues.

## Error handling

- Subagent failure / timeout / budget-exceeded → captured as a structured error
  inside the join result. In parallel mode siblings are unaffected; in sequential
  mode the coordinator decides whether to continue. The coordinator always sees
  partial results.
- Depth or budget exhaustion → `spawn_agent` returns a refusal result.
- Malformed `spawn_agent` args → validation error returned as tool result, no
  crash.

## Testing

- **Unit** (`tests/test_subagent_orchestrator.py`): sequential dispatch,
  parallel fan-out + join, depth cap enforcement, budget cap enforcement, tool
  scoping intersection, persona/agent resolution, owner-id derivation, SOUL.md
  injection, untrusted tagging of injected identity + subagent results,
  multi-owner memory read (`owner IN (agent_id, human_id, NULL)`), and the
  cross-user guard (agent cannot read another human's owner).
- **Integration**: a real 2-subagent run (small local model, or a mocked LLM)
  verifying the owner model (agent reads human + shared, writes to agent_id) and
  correct join.
- Reuse the existing pytest harness under `tests/`.

## Files

NEW:
- `src/subagent_orchestrator.py` — dispatch, scoping, depth/budget/concurrency
  bounds, join.
- `services/agents/profile.py` — load persona `SOUL.md` + agent `agent.json`,
  derive the `agent:{human}/{name}` owner id.
- `data/personas/`, `data/agents/` — persona and agent definitions.

EDIT:
- `src/tool_index.py` — register `spawn_agent`.
- `src/tool_implementations.py` — `spawn_agent` impl → orchestrator.
- `src/agent_loop.py` — depth-aware prompt assembly hook, SOUL.md injection,
  nested-loop entry point.
- `src/settings.py` — defaults `agent_max_parallel=2`, `agent_max_depth=2`.
- `core/session_manager.py` — child-session support for `persist=true`:
  `metadata.parent_session_id` + `metadata.human_owner` + `metadata.kind="subagent"`
  (reuse `ensure_task_session`; no new store).
- `src/memory.py` — multi-owner read helper (accept a list of owners) for the
  `owner IN (agent_id, human_id, NULL)` read; writes already take an explicit owner.
- `src/auth_helpers.py` (or auth manager) — reject `agent:`-prefixed ids on the
  login path; treat them as internal-only owners.

TESTS:
- `tests/test_subagent_orchestrator.py`.

## Out of scope (later slices)

- Full Agent Profiles subsystem from #724 (isolated chat history, per-profile model
  config UI, profile switching).
- Any UI.
- Voice wiring (handled separately; local STT already enabled, Kokoro TTS deferred).
```
