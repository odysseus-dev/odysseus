# Odysseus one-chat OpenHands UX

**Date:** 2026-09-09  
**Status:** Approved for planning  
**Parent:** `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`  
**Branch:** `design/openhands-agent-platform-cutover`

## 1. Decision

Odysseus chat is the only interactive product surface. OpenHands Agent Server is the only interactive execution backend. Agent Canvas remains a separate engineering UI for the same conversation and is not rebuilt, embedded, or feature-matched in Odysseus.

This slice removes the Gate 10 dual-composer (`#agent-platform-panel` / `?agents=1`). It does not start 9router. It does not delete `src/agent_loop.py` (Task 18 remains held).

## 2. Goals

- One composer and one transcript: the existing Odysseus chat bar and message list.
- Every interactive send in that composer dispatches through OpenHands (`AgentDispatcher` / Agent Server `continued_run`).
- OpenHands owns the canonical conversation and events. Odysseus stores a session-to-conversation binding and a product projection, not a second source of truth.
- Chat-bar lifecycle: send, Approve, Cancel, Resume, and a Canvas link for the bound conversation.
- Odysseus choosers in this slice: **chat type** and **agent type**.
- Leave the existing model selector visible and unmodified until the 9router slice.

## 3. Non-goals

- 9router, a single subscription transport, or any model-picker change (copy, options, wiring, or hide).
- Hermes as a selectable agent type (deferred until 9router).
- Embedding Canvas, hosting Canvas routes, or porting Canvas features (files, LLM settings, agent admin, diagnostics, home chips).
- Deleting `src/agent_loop.py` / `execute_tool_block` or finishing Task 18.
- Recreating the side-panel second textarea or keeping `?agents=1` as a product flag.
- Changing scheduled/background Automation ownership.

## 4. Ownership

| Concern | Owner |
|---|---|
| Product chat UI (composer, transcript, type pickers) | Odysseus |
| Chat type (archetype / Agent vs Chat) | Odysseus |
| Agent type (OpenHands profile) | Odysseus policy → OpenHands profile |
| Model / subscription | Unchanged Odysseus picker until 9router |
| Canonical conversation and events | OpenHands Agent Server |
| Execution lifecycle (run, interrupt, resume, confirmation) | OpenHands Agent Server |
| ApprovalGrant signing | Odysseus |
| Full engineering UI | Agent Canvas (link only) |

## 5. Product UI

### 5.1 Composer

The existing Odysseus composer is the only input. First send on a session creates an Agent Server conversation. Later sends resume that conversation (`continued_run`). There is no separate Launch control and no second input.

### 5.2 Chat type

Chat type is the product archetype already expressed by the Agent / Chat toggle:

- **Agent** → archetype `chat` with tools enabled (today’s agent mode).
- **Chat** → same OpenHands conversation path, restricted tool policy (today’s chat mode).

Deep Research stays on its existing entry point and archetype. It is not redesigned here.

### 5.3 Agent type

Agent type selects the OpenHands profile for the next execution:

- **Native** (`odysseus` / OpenHands native agent)
- **OpenCode** (`opencode`)

Hermes is not offered. Switching agent type on an already-started OpenHands conversation follows upstream rules: if the live conversation cannot change profile, Odysseus starts a new conversation and rebinds the session, and says so in the transcript. It does not silently fork a second visible chat.

### 5.4 Model selector

The current “Select a model” control stays as-is. This slice must not restyle, relocate, disable, or rewire it. 9router will replace model selection later.

### 5.5 Lifecycle controls

On the same chat bar, not a side panel:

- **Send** — existing send; creates or continues the bound OpenHands execution.
- **Approve** — visible when a pending `ActionEvent` needs confirmation; `409` if none.
- **Cancel** — interrupts the current execution, not the conversation.
- **Resume** — continues a paused execution on the same conversation.
- **Open in Canvas** — `target=_blank` link to the bound conversation. Hidden until a conversation id exists.

Remove `#agent-platform-panel`, its textarea, and `?agents=1` unhide. `initAgentPlatform` binds the chat-bar controls instead.

### 5.6 Transcript

The existing message list is the product projection. Assistant text, tool/approval cards, and status come from reconciled OpenHands events plus Odysseus domain cards. Odysseus must not keep a parallel “OpenHands chat” thread in the sidebar.

## 6. Binding and data flow

1. Odysseus authenticates the user and resolves chat type (archetype + tool policy) and agent type (profile).
2. The session row stores `openhands_conversation_id` once created. Rebinds are explicit (agent-type change that cannot continue in place).
3. Send calls the existing chat route, which already enters `stream_governed_agent`. That path must dispatch via `AgentDispatcher` and then stream projected events until the execution is idle, not return `[DONE]` after create-only.
4. Projection (`ProjectionReconciler`) updates the session transcript and chat-bar status (running, paused, pending confirmation, degraded).
5. Approve / Cancel / Resume use `/api/agents/executions/{id}/…` against the bound execution id (`agent-server:{conversation_id}` or the current execution id from the last dispatch).
6. Canvas URL is the existing `OPENHANDS_CANVAS_URL` conversation link.

`stream_governed_agent` today emits an execution ref and `[DONE]`. This slice makes that function the product stream: it waits on Agent Server events and yields the same SSE shapes the Odysseus transcript already consumes, plus an `execution` event so the bar can bind Approve / Cancel / Resume / Canvas.

## 7. Errors

- Agent Server unreachable: existing chat error surface; Canvas link remains if a conversation was already bound.
- No pending confirmation on Approve: `409`, bar stays put, no fake `{approved: true}`.
- Cancel targets execution only.
- Profile switch that cannot continue: new conversation, rebound session, user-visible note.
- Auth: login `next` may still preserve query strings; `?agents=1` is ignored and does not unhide a second composer.

## 8. Testing

- JS: panel absent; controls live on the chat bar; model selector markup unchanged; no `?agents=1` gate.
- Routes: send on a session creates or resumes one conversation; second send reuses the binding; Approve without `ActionEvent` is `409`; Cancel does not cancel the conversation.
- Client: `autotitle: false` remains on Odysseus-created conversations.
- No Canvas iframe in `index.html`.
- Existing model-selector tests stay green without edits unless a collision is unavoidable.

## 9. Held work

- Task 18: backup, one switch, clean disposition ledger, then delete the old loop.
- 9router: model selector and Hermes.
- Canvas feature work.
