# Clicky Operator Voice Bridge — Tasks

> [x] done in working tree · [~] done but left UNCOMMITTED (clicky_chat.py is
> untracked pre-existing WIP; memory_stack.env is local config) per prior owner
> decision. Committed: this openspec change + tests/test_clicky_agent_bridge.py.

## 1. Bridge implementation

- [~] 1.1 Add `stream_agent_chat(body)` to `clicky_integration/clicky_chat.py`: resolve endpoint, convert body → messages, run `stream_agent_loop` with the scoped operator tool set, translate events → Anthropic SSE
- [~] 1.2 Add `_clicky_session_id()`, `_clicky_owner()`, `_operator_consent_enabled()`, `_clicky_max_rounds()` config helpers + pure `translate_agent_chunk()`
- [~] 1.3 Pre-grant operator consent (`services.operator.core.grant_consent`) for the Clicky session when `CLICKY_OPERATOR_CONSENT` is truthy
- [~] 1.4 Dispatch `mode == "agent"` in `stream_clicky_chat`
- [x] 1.5 Worker `/health` already surfaces `chat_mode` — no change needed

## 2. Config

- [~] 2.1 `memory_stack.env`: documented `CLICKY_CHAT_MODE` (endpoint|memory|agent), `CLICKY_OPERATOR_CONSENT=false`, `CLICKY_CHAT_OWNER`, `CLICKY_AGENT_MAX_ROUNDS=8`

## 3. Tests  (tests/test_clicky_agent_bridge.py — 15 tests, all green)

- [x] 3.1 Text deltas → Anthropic `content_block_delta`; thinking deltas suppressed
- [x] 3.2 Tool machinery events (`tool_start`/`tool_output`/`agent_step`/`metrics`/`model_actual`) not spoken
- [x] 3.3 `[DONE]` terminates; error event + error type → spoken error
- [x] 3.4 Scoped `relevant_tools` includes the six operator tools; stable session id
- [x] 3.5 Consent pre-granted only when the flag is set
- [x] 3.6 `stream_clicky_chat` dispatches `agent` mode; `endpoint` mode unchanged

## 4. To use it

- [ ] 4.1 Set `CLICKY_CHAT_MODE=agent` (and `CLICKY_OPERATOR_CONSENT=true` for actions) in `memory_stack.env`, restart the Clicky worker, then speak commands
