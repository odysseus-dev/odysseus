# Add Clicky Operator Voice Bridge

## Why

The agentic operator (`add-agentic-operator`) wired six tools into the Odysseus
agent loop, and Clicky acts as the desktop *executor* (mouse/audio) when that
loop runs `desktop_act`. But Clicky's own `/chat` — the voice overlay the user
actually talks to — is a single-shot model relay (`stream_llm`) with no tool
loop, so speaking a command to Clicky can never invoke `screen_look`,
`browser_act`, `desktop_act`, or the other operator tools. The user's intent is
to talk to Clicky and have it act. This bridges that gap.

## What Changes

- New Clicky chat mode **`agent`**: route the Clicky worker's `/chat` through
  `stream_agent_loop` (the full Odysseus agent loop with tools) instead of the
  single-shot `stream_llm` relay, translating the agent-loop event stream back
  into the Anthropic SSE text the clicky-windows client expects.
- **Deterministic tool scope**: pass an explicit `relevant_tools` set (the six
  operator tools + `web_search` + `manage_memory`) so the loop skips its
  ChromaDB retrieval — the Clicky worker runs as a separate process that may not
  have the tool index, and voice commands should reach the operator reliably.
- **Voice consent model**: because a voice overlay has no place to render the
  `ask_user` consent prompt, pre-grant operator action consent for the Clicky
  session, gated behind an explicit opt-in env flag (`CLICKY_OPERATOR_CONSENT`).
  Holding push-to-talk and speaking the command *is* the consent. Off by default.
- **Config**: `CLICKY_CHAT_MODE=agent`, `CLICKY_OPERATOR_CONSENT`, optional
  `CLICKY_CHAT_OWNER` in `memory_stack.env`.

## Capabilities

### New Capabilities
- `clicky-agent-bridge`: Route Clicky voice chat through the Odysseus agent loop
  with a scoped operator tool set and a voice-appropriate consent model, so
  spoken commands invoke the operator tools and stream a spoken answer back.

### Modified Capabilities
<!-- None: desktop-action/browser-action requirements are unchanged; this adds a
     new front-end path into the existing agent loop. -->

## Impact

- **Code**: `clicky_integration/clicky_chat.py` (new `stream_agent_chat` +
  `agent` mode dispatch in `stream_clicky_chat`); reuses `src.agent_loop`,
  `services.operator.core.grant_consent`, existing endpoint resolution.
- **Config**: `memory_stack.env` (mode + consent flag + owner).
- **No new deps**; no changes to the operator services or their specs.
- **Tests**: agent-loop → Anthropic SSE translation, tool-event swallowing,
  consent pre-grant behind the flag, scoped tool set, mode dispatch.
- **Out of scope**: two-turn spoken consent negotiation (v1 uses the opt-in
  pre-grant); rendering tool progress as speech beyond the model's own narration.
