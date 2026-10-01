# Gate 10: macOS acceptance stack

Date: 2026-09-10 (America/Los_Angeles)  
Probe: `python3 scripts/openhands_probe.py acceptance --json` → `passed: true`, `live_stack: true`  
Tailscale Serve: `8443` → `127.0.0.1:7001`, `8444` → `127.0.0.1:8000`

Odysseus is the product chat. OpenHands Agent Server is the interactive
backend. Canvas stays a separate UI (link only). Hermes ACP and the
Odysseus model picker rebuild are **not** part of this gate; they are the
9router slice that this evidence unblocks.

No credentials or raw secret values are recorded here.

## Stack

| Service | Result |
|---|---|
| Odysseus | healthy, `http://127.0.0.1:7001/api/health` 200 |
| Agent Server | healthy (unpublished) |
| Automation | healthy (unpublished) |
| Canvas frontend-only | healthy, Tailscale `8444` 200 |
| Odysseus MCP | healthy |
| Tailscale Odysseus | `https://mac-server-cli.tail09a270.ts.net:8443/api/health` 200 |

## Product checks

| Check | Result | Evidence |
|---|---|---|
| Notes exist | pass | unauthenticated `/api/notes` is **401** (not 404); authenticated GET **200** |
| Documents exist | pass | unauthenticated `/api/documents/library` is **401** (not 404); authenticated GET **200** (`documents: []`) |
| Native Odysseus → OpenHands chat | pass | `POST /api/chat_stream` `mode=agent` `agent_profile_id=odysseus` with empty session model; SSE `execution` + delta `pong` + `[DONE]` in 2.5s; session `92235231-…` bound to `f84bbee5-3333-421d-a5d1-713615a269c7` |
| OpenCode ACP | pass | prior walk `a64e8b13-…` finished |
| Cancel / resume | pass | prior walk interrupt → `paused`, resume → `finished` |
| Approval | pass (code) | `POST …/approve` is 409 without `ActionEvent`; pending events call `respond_to_confirmation` |
| Scheduling | pass (path + inventory) | 11 `scheduled_tasks` (5 active, 6 paused), all `task_type=action`; fire path is `stream_governed_agent`. Live ticks of user tidy/email tasks were not executed |
| Restart / reconciliation | pass (in-process) | `tests/agents/test_projection.py` projector restart + unknown-event quarantine |
| Hermes ACP | deferred | `ACPInitError: No LLM provider configured`; Canvas ChatGPT subscription does not apply. Profile `hermes` remains; 9router owns provider wiring |
| Odysseus research toggle | deferred | Chat-mode research still requires the model picker; same 9router slice |
| Task 18 deletion | held | backup, one switch, clean ledger; see `legacy-deletion-hold.md` |

## Parser contract (Agent Server 1.45)

Odysseus chat must read live events, not the older stub shapes:

- assistant text from `MessageEvent.llm_message.content[]`
- idle from `ConversationStateUpdateEvent` `key=execution_status` / `value=finished` (and from conversation `execution_status`)
- empty first poll must not end the stream

Covered by `tests/agents/test_legacy_bridge.py`. Agent mode must not require
the Odysseus model picker (`tests/test_chat_openhands_binding.py`).

## Next slice

9router: model selector, subscription transport, Hermes ACP provider, Odysseus
research that uses that transport. Do not start Task 18 deletion until that
slice and the coordinated-cutover hold are satisfied.
