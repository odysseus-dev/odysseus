# Odysseus operations chat on OpenHands

**Date:** 2026-09-10  
**Status:** Draft for review  
**Parents:**
- `docs/superpowers/specs/2026-09-08-openhands-agent-platform-cutover-design.md`
- `docs/superpowers/specs/2026-09-09-odysseus-one-chat-openhands-design.md`

This slice implements the one-chat spec’s Chat path (parent §5.2) and records the operations-vs-engineering UI split. It does not start 9router. It does not delete `src/agent_loop.py` (Task 18 remains held). Do not edit parent spec bodies; this file is the delta.

## 1. Decision

Odysseus is the **operations platform**: a clean product UI for running agentic work (notes, mail, calendar, tasks, research, chat) against OpenHands Agent Server. Agent Canvas is the **engineering UI**: where operators add agents, add workflows, change LLM settings, and otherwise modify the runtime. Odysseus does not grow Canvas screens. OpenHands does not become a second product chat.

Interactive Chat mode and Agent mode are the same OpenHands conversation. Chat is a restricted tool policy, not a parallel `stream_llm` pipe. Odysseus never accepts inbound model completions as a gateway. OpenHands and the browser still call into Odysseus for MCP and the product UI.

`OdysseusAgent` stays composition (`CodeActAgent` + system suffix + Odysseus MCP + profile policy), not a subclass, until composition is proven insufficient (parent cutover §8).

### Analogy (UI only)

OpenHands / Canvas is work that improves the machine (a 3D printer that prints better printer parts). Odysseus is work that uses the machine for production (parts for the factory). Odysseus benefits from Canvas upgrades; the two UIs are not the same job. This analogy does **not** restrict chat topics. An Odysseus conversation may discuss agents, workflows, or Canvas. Odysseus simply does not port Canvas’s engineering GUI, so those jobs are easier there.

## 2. Goals

- Composer Chat send uses `stream_governed_agent` / Agent Server, same binding as Agent send.
- Chat type remains the existing Agent / Chat toggle: Agent = tools on; Chat = no tools and no document-tooling, still OpenHands events.
- Interactive send does not require an Odysseus `ModelEndpoint`. OpenHands keeps its configured LLM until 9router.
- Product UI stays operations-shaped: send, transcript, Approve / Cancel / Resume, Native / OpenCode, Canvas link. No “add agent”, “add workflow”, LLM-admin, or Canvas diagnostics in Odysseus.
- Canvas remains `target=_blank` for the bound conversation. Copy may say engineering changes are easier in Canvas; chat is not filtered or sanctioned for those topics.
- Agent Server unavailable: no local `stream_llm` fallback for interactive chat (cutover §20).

## 3. Non-goals

- 9router, subscription transport, or any model-picker restyle / rewire / hide.
- Hermes as a selectable agent type.
- Embedding Canvas, hosting Canvas routes, or porting Canvas tools (files, LLM settings, agent admin, workflow editor, home chips, diagnostics).
- Blocking, warning, or routing away Odysseus chats because the user talks about agents or workflows.
- Subclassing OpenHands agents.
- Deleting `src/agent_loop.py` / `execute_tool_block` (Task 18).
- Changing Odysseus MCP (agents still call in for domain tools).
- Token-auth `POST /api/v1/chat` (`routes/webhook/webhook_routes.py`); leftover unmanaged `llm_call_async`. Out of this slice.
- Model jobs (titles, extract), embeddings, STT/TTS, image generation.
- Scheduled/background Automation ownership.

## 4. Ownership

| Concern | Owner | Surface |
|---|---|---|
| Run production work (chat, mail, notes, tasks) | Odysseus UI | operations |
| Product policy, domain data, ApprovalGrant, MCP | Odysseus | not a skin |
| Canonical conversation, events, interrupt, resume | Agent Server | runtime |
| Add agent, add workflow, LLM admin, runtime diagnostics | Agent Canvas | engineering |
| Native vs OpenCode profile for the next turn | Odysseus chooser → OpenHands profile | operations |
| Model / subscription | Unchanged picker until 9router | later |

## 5. Chat vs Agent

Live `routes/chat_routes.py` still calls `stream_llm_with_fallback` when `chat_mode == "chat"`. That path is removed for the interactive composer.

- First Chat or Agent send on a session creates or resumes one Agent Server conversation.
- Chat and Agent share `openhands_conversation_id`. Switching type does not fork a second sidebar thread.
- Chat: OpenHands run with tools disabled (today’s “NO tools, NO document access”).
- Agent: today’s governed agent path.
- Auto-escalation Chat→Agent stays. It no longer means “leave OpenHands.”
- Native (`odysseus`) and OpenCode (`opencode`) remain the agent-type chooser. Hermes stays deferred.

## 6. Data flow

1. Browser `POST /api/chat_stream` with `mode=chat` or `mode=agent`.
2. Route binds via `conversation_kwarg` / `resolve_agent_profile_id` (unchanged).
3. Both modes call `stream_governed_agent` with a per-turn `turn_id`. Chat passes a restricted tool policy; Agent passes the existing policy.
4. SSE: `execution`, `delta`, `pending_confirmation` (Agent; Chat should not emit tool confirmations), `[DONE]`.
5. Session stores `openhands_conversation_id` and `agent_profile_id` on `type==execution`.

Odysseus does not add an OpenAI-compatible inbound completions API. Domain MCP and authenticated product HTTP remain.

## 7. Errors

- Agent Server unreachable: existing agent error surface; no `stream_llm` fallback.
- Chat send with no Odysseus model selected: allowed (OpenHands owns the LLM for this slice).
- Approve without `ActionEvent`: `409` (unchanged). Chat mode should not need Approve for ordinary turns.
- Canvas unavailable: Odysseus operations continue; the Canvas link may fail independently.

## 8. Testing

- Chat composer send creates or reuses an OpenHands conversation and does not call `stream_llm` / `stream_llm_with_fallback`.
- Restricted Chat policy: no tool / document-tool invocation on Chat turns.
- Agent send behavior and binding tests stay green.
- `#model-picker-wrap` markup unchanged; existing picker tests stay green without edits unless a collision is unavoidable.
- `index.html` has no Canvas iframe and no new “add agent” / “add workflow” controls.
- No test asserts that Odysseus chat text is blocked for engineering topics.

## 9. Held work

- 9router: picker, subscription transport, Hermes provider URL (OpenHands/Hermes as 9router clients, not an Odysseus gateway).
- Task 18: backup, one switch, clean ledger, delete the old loop.
- `POST /api/v1/chat` unmanaged LLM path.
- Canvas feature work.
- OdysseusAgent subclassing.
