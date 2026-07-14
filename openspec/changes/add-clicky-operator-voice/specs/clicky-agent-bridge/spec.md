# clicky-agent-bridge — Spec Delta

## ADDED Requirements

### Requirement: Clicky agent chat mode
The Clicky worker `/chat` SHALL support a mode `agent` (selected by
`CLICKY_CHAT_MODE=agent`) that routes the request through the Odysseus agent
loop (`stream_agent_loop`) rather than the single-shot `stream_llm` relay, so
the model can invoke tools. The existing `endpoint` and `memory` modes remain
unchanged and default.

#### Scenario: Agent mode dispatches to the agent loop
- **WHEN** `CLICKY_CHAT_MODE=agent` and a `/chat` request arrives
- **THEN** the request is handled by the agent-loop path, and tool calls the model emits are executed

#### Scenario: Default mode is unchanged
- **WHEN** `CLICKY_CHAT_MODE` is unset or `endpoint`
- **THEN** `/chat` uses the existing single-shot relay and no tools run

### Requirement: Agent events translated to Anthropic SSE
The bridge SHALL translate the agent loop's event stream into the Anthropic SSE
the clicky-windows client consumes: assistant text deltas become
`content_block_delta` text deltas; tool machinery events (`tool_start`,
`tool_output`, `agent_step`, `metrics`, and similar) are not spoken; the stream
ends with `[DONE]`. Reasoning/thinking deltas MUST NOT be spoken.

#### Scenario: Text is spoken, tool machinery is not
- **WHEN** the agent loop yields text deltas interleaved with `tool_start`/`tool_output` events
- **THEN** the Clicky response contains only the assistant's natural-language text as Anthropic deltas, followed by `[DONE]`

#### Scenario: Errors are voiced
- **WHEN** the agent loop yields an error event
- **THEN** the bridge emits a spoken error message rather than a silent failure

### Requirement: Deterministic operator tool scope
The bridge SHALL pass an explicit tool set (the six operator tools plus
`web_search` and `manage_memory`) to the agent loop so that tool availability
does not depend on the Clicky worker process having the ChromaDB tool index.

#### Scenario: Operator tools reachable without the tool index
- **WHEN** a Clicky agent-mode request runs in a worker process with no tool index available
- **THEN** the operator tools are still offered to the model because they were passed explicitly

### Requirement: Voice consent via opt-in pre-grant
Because a voice overlay cannot render the `ask_user` consent prompt, the bridge
SHALL pre-grant operator action consent for the Clicky session only when
`CLICKY_OPERATOR_CONSENT` is explicitly enabled; otherwise action tools return
their normal `consent_required` result. The flag is off by default.

#### Scenario: Consent flag enabled
- **WHEN** `CLICKY_OPERATOR_CONSENT` is truthy and the user speaks "click the submit button"
- **THEN** `desktop_act` executes without a separate approval step because consent was pre-granted for the Clicky session

#### Scenario: Consent flag disabled
- **WHEN** `CLICKY_OPERATOR_CONSENT` is unset and the user speaks an action command
- **THEN** the action tool returns `consent_required` and no desktop/browser mutation occurs

### Requirement: Stable Clicky session identity
The bridge SHALL use a stable session id for Clicky agent runs so that consent
and the operator audit log are coherent across turns, and SHALL record actions
under that session in the operator audit log like any other agent-driven action.

#### Scenario: Actions audited under the Clicky session
- **WHEN** a Clicky agent-mode command executes a desktop or browser action
- **THEN** an `operator_audit` row is written attributed to the Clicky session id
