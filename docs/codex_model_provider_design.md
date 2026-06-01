# Codex Model Provider Draft

Status: research/prototype only. Not ready for PR.

This branch intentionally keeps Codex model/provider work separate from the
Codex / ChatGPT device-code authentication branch. The auth branch should stay
focused on sign-in and sign-out. Model picker, chat, and agent integration need
their own design because a naive `codex exec --json` provider would not match
Odysseus' streaming, session, or tool-round contracts.

## Current Model Architecture

Odysseus model selection is built around admin-configured `ModelEndpoint` rows.
The table shape in `core/database.py` is:

- `id`: endpoint identifier used by settings, sessions, and picker payloads.
- `name`: display label.
- `base_url`: provider base URL such as `http://localhost:8002/v1` or
  `https://openrouter.ai/api/v1`.
- `api_key`: encrypted optional provider API key.
- `is_enabled`: endpoint visibility switch.
- `hidden_models`: JSON list of model ids hidden after probe failures.
- `cached_models`: JSON list of last discovered model ids.
- `model_type`: `llm` or `image`.
- `supports_tools`: nullable function-calling capability override.
- `owner`: optional user scoping; admins see all endpoints.

`routes/model_routes.py` owns the API surface:

- `/api/model-endpoints`: admin CRUD over `ModelEndpoint` rows.
- `/api/model-endpoints/test`: one-off model-list/ping validation.
- `/api/model-endpoints/{id}/probe`: SSE probe over chat-capable models.
- `/api/model-endpoints/{id}/models`: model visibility management.
- `/api/models`: user-scoped model picker payload. It emits endpoint-shaped
  items with `url`, `models`, `models_extra`, `endpoint_id`, `endpoint_name`,
  `category`, `model_type`, and `offline`.
- `/api/default-chat`: resolves the default chat endpoint/model with per-user
  scoping and fallback settings.

Provider detection is URL based. `src/llm_core.py` and
`src/endpoint_resolver.py` infer OpenAI-compatible, Anthropic, OpenRouter,
Groq, and Ollama behavior from the endpoint URL. HTTP calls are then built as:

- OpenAI-compatible: `{base}/chat/completions`, SSE deltas from
  `choices[0].delta.content`, accumulated `tool_calls`, optional usage chunks.
- Anthropic: native `/v1/messages`, translated message/tool schema, native
  SSE event parsing.
- Ollama: native `/api/chat`, newline JSON streaming, native tool call
  accumulation.

The selected model path is:

1. Settings/admin UI creates or updates a `ModelEndpoint`.
2. `/api/models` returns visible endpoint items.
3. `static/js/models.js` and `static/js/modelPicker.js` render rows and store
   `endpoint_url`, `model`, and `endpoint_id` on a session.
4. `routes/chat_routes.py` receives `/api/chat` or `/api/chat_stream`.
5. `routes/chat_helpers.py` prepares context, validates privileges, and
   normalizes model ids.
6. Plain chat calls `stream_llm_with_fallback`.
7. Agent mode calls `stream_agent_loop`, which wraps `stream_llm_with_fallback`
   across multiple tool rounds.

Fallbacks are configured as endpoint/model pairs in settings. The resolver
turns them into `(chat_url, model, headers)` tuples. Streaming fallback only
switches before any real content is emitted, so duplicate partial answers are
avoided.

## Codex CLI Findings

The requested help probes were attempted:

- `codex --help`
- `codex login status`
- `codex exec --help`

On this Windows host, `codex.exe` resolves to:

`C:\Program Files\WindowsApps\OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0\app\resources\codex.exe`

All three commands failed before producing help text with PowerShell reporting
`Access is denied`. That means this runtime cannot validate `codex exec`
capabilities directly. The branch must not depend on undocumented behavior from
this CLI install.

Reliable enough today:

- Existing `src/codex_auth.py` can report CLI presence, executable status, and
  `codex login status` output when the binary is executable.
- Auth status does not read, parse, store, log, or expose raw Codex tokens.
- The integration can safely build capability/status reporting around the auth
  service.

Not reliable enough today:

- JSON event shape from `codex exec`.
- Whether true token deltas exist.
- Session/resume identity and lifecycle.
- Tool execution controls suitable for a chat-provider mode.
- Docker runtime behavior, because the current host binary is inaccessible and
  the project should not bundle the CLI in this branch.

## Proposed Integration

Do not add Codex as a normal `ModelEndpoint` yet. The current endpoint schema is
URL/API-key oriented and does not have a clean auth mode field such as
`auth_type = api_key | codex_cli`. Overloading `base_url` with a fake URL would
make picker selection look like a normal provider while chat routing still has
different semantics.

Instead, stage the work:

1. Keep Codex sign-in in Settings -> Integrations.
2. Add a feature-flagged internal Codex model-provider capability probe:
   `ODYSSEUS_CODEX_MODEL_PROVIDER_ENABLED=false` by default.
3. Report a synthetic model only when the feature flag is enabled, the CLI is
   available, and Codex auth status is authenticated.
4. Mark chat support, token streaming, session resume, and tool execution as
   unsupported until each has a concrete implementation and tests.
5. Only later add schema support for a provider auth type or a first-class
   non-HTTP provider registry.
6. Only after that expose Add Models / model picker UI, with clear experimental
   copy and sign-in-required state.

The Add Models UI should link to the existing Integrations Codex form instead
of duplicating auth implementation. The sign-in state is host-wide and
admin-gated, so a second auth card in Add Models would increase security and UX
surface area without adding capability.

## Hook Points

Minimum clean hook points:

- `src/codex_auth.py`: source of truth for CLI availability and auth state.
- A new internal provider module for feature flag, capability shape, and
  eventual adapter boundary.
- A new admin-gated status route, separate from `/api/models`, until chat
  behavior is ready.

Future hook points after adapter design:

- Provider registry used by `/api/models`, returning endpoint-shaped entries
  with explicit metadata such as `experimental`, `requires_sign_in`, and
  `streaming_supported`.
- `src/llm_core.py` or a sibling dispatch layer that routes non-HTTP providers
  without pretending they are OpenAI-compatible URLs.
- `routes/chat_routes.py` plain-chat branch only after session/resume and
  streaming semantics are explicit.
- Agent mode only after tool execution is intentionally disabled or mapped to
  Odysseus agent rounds.

## Test Plan

Current stage:

- Feature flag disabled returns no synthetic models.
- Feature flag enabled but unauthenticated returns sign-in-required.
- Feature flag enabled and authenticated returns a clearly experimental model.
- Capability responses expose no token fields.
- CLI unavailable state is reported clearly.
- Existing Codex auth tests continue passing.
- Existing model route tests continue passing.

Deferred before PR:

- Mocked Codex CLI adapter success/failure/timeout behavior.
- No fake token streaming.
- Session/resume behavior.
- Explicit tool execution isolation.
- UI gated behind feature flag.
- Docker validation with sign-in, model selection, chat call, logout, and
  failure states.

## Readiness

Not ready for PR. This branch can document the architecture and carry a small
internal capability probe. It should not claim full Codex-backed chat/provider
support until streaming, session/resume, tool isolation, and Docker behavior are
implemented and validated end to end.
