# LLM Models And Endpoints

Last updated: dev@0750486 | 2026-06-15

## Scope

This spec covers model/provider behavior in:

- `src/llm_core.py`;
- `src/endpoint_resolver.py`;
- `src/model_discovery.py`;
- `src/model_context.py`;
- `src/task_endpoint.py`;
- `src/tls_overrides.py`;
- `src/copilot.py`;
- `routes/copilot_routes.py`;
- `routes/chatgpt_subscription_routes.py` and `routes/device_flow.py`;
- `routes/model_routes.py`;
- `routes/session_routes.py`;
- `routes/cookbook_routes.py`, `routes/hwfit_routes.py`, and `services/hwfit/`;
- `src/settings.py`;
- `core/database.py` model `ModelEndpoint`;
- frontend modules `static/js/models.js`, `static/js/modelPicker.js`, `static/js/model/matchKey.js`, `static/js/providers.js`, `static/js/settings.js`, `static/js/admin.js`, `static/js/compare/`, and Cookbook model-serving modules;
- chat, compare, research, STT/TTS, and utility-model call sites.

## Provider Calls

`src.llm_core` owns provider-call mechanics. It handles OpenAI-compatible calls, Ollama normalization, Anthropic payload conversion, GitHub Copilot and ChatGPT Subscription provider detection/header injection, NVIDIA provider routing, streaming, fallback calls, upstream error formatting, async/streaming host liveness caching, configured model-list cache reads, tool-call sanitization, reasoning/thinking stream routing, and provider-specific parameter rules. GitHub Copilot OAuth/device-flow orchestration lives in `routes/copilot_routes.py` and `src/copilot.py`; ChatGPT Subscription device flow uses `routes/chatgpt_subscription_routes.py`, shared device-flow helpers, and `ProviderAuthSession` rows.

`llm_core` owns payload shape. Route files and chat/agent code should request a call; they should not duplicate provider-specific payload quirks.

Provider-specific behavior is part of this layer: `LLM_CONNECT_TIMEOUT` controls the connect budget for sync and streaming calls, Kimi Code endpoints retry a small whitelisted User-Agent set on 403 and cache the accepted value, official Moonshot/Kimi Code and Anthropic Opus 4.7+ payloads omit `temperature` where required, and self-hosted compatible endpoints keep normal OpenAI-compatible parameters unless detected otherwise.

Route-level probe helpers in `routes/model_routes.py` are the current exception: they build minimal provider-specific probe payloads using `llm_core` detection helpers. Keep probe behavior aligned with `llm_core` provider adapters. LLM provider HTTP clients and endpoint probes share `src.tls_overrides.llm_verify()`, which can add an operator-provided `LLM_CA_BUNDLE` on top of normal certificate verification without turning verification off or widening that trust to arbitrary URL fetches.

## Endpoint Resolution

`src.endpoint_resolver` owns endpoint normalization and URL construction:

- base URL normalization;
- chat and model-list URL construction;
- endpoint ID resolution;
- chat, utility, and vision fallback candidate selection;
- Tailscale hostname resolution where available.

OpenAI-compatible model-list URL construction preserves `/v1` bases and inserts `/v1/models` for bare local bases such as LM Studio `http://localhost:1234`.

`routes/model_routes.py` owns model endpoint CRUD, admin provider discovery/probing, visible/hidden/pinned model lists, endpoint kind and refresh policy, curated/extra model partitioning, `/api/models` catalog caching, Docker loopback rewriting, tool-support probing, endpoint-dependent settings cleanup, and owner filtering. Endpoint dedupe allows the same base URL under different API keys and surfaces API-key fingerprints/key presence without returning secrets.

`routes/session_routes.py` owns binding sessions to endpoint IDs, owner-scoped header construction, raw-endpoint rejection for non-admin users, model validation, and persisted session headers. Compare panes and normal chat session creation use this path.

`ModelEndpoint` rows own API keys, base URLs, cached/hidden/pinned models, model type, endpoint kind, refresh mode/interval/timeout, supports-tools state, nullable owner, optional provider-auth linkage, and provider metadata. `owner = NULL` means legacy/shared; non-null rows are private to that owner, while admins can see all. Secret fields must remain encrypted and scrubbed in responses.

Decrypted endpoint headers can be copied into session metadata for chat use. Endpoint deletion must clear dependent settings and copied session headers.

## Model Discovery And Lists

`src.model_discovery` owns host/env/Tailscale/local-port scanning for model servers. Admin `/api/providers` and `/api/discover` use that scanner; endpoint CRUD, test, refresh, and hidden-model controls are frontend-owned by `static/js/admin.js`.

`/api/models` is the normal picker/catalog surface. It is auth/owner scoped, per-user/admin-flag cached briefly, can trigger background refresh, preserves offline endpoint rows, filters hidden models, and preserves pinned model IDs for UI selection. API-token callers must carry `chat` scope and a token owner before they can list models. Proxy/API endpoints can be marked cached-first/manual so large upstream catalogs are not repeatedly probed, while explicit refresh paths use longer manual timeouts. `static/js/models.js` and `static/js/modelPicker.js` own the sidebar/picker catalog; `static/js/model/matchKey.js` owns longest-substring model-info/pricing key matching; `static/js/settings.js` owns default, utility, vision, image, TTS, STT, and fallback selectors.

`src.task_endpoint` owns background-task endpoint/model resolution for task routes and scheduler callers. It resolves `task_endpoint_id`/`task_model` through the normal endpoint resolver with owner context.

Cookbook and HWFit own local model download, serve, ranking, and auto-registration flows. They can create LLM or image `ModelEndpoint` rows, but provider dispatch remains owned by `llm_core`/endpoint resolution.

## Context Length

`src.model_context` owns model context-length lookup/query and token estimation. Cache keys include endpoint plus model so identical model names on different endpoints do not bleed context-window data. Unknown context lengths are explicit unknowns rather than default values; known lengths feed chat/agent token-budget scaling through `src.context_budget`. Token estimation counts `tool_calls` so compaction sees tool-only turns instead of underestimating them. Chat/agent context budgeting should call this layer instead of hardcoding model windows.

## Runtime Fallback And Routing

Streaming chat and agent mode use configured fallback candidates through `stream_llm_with_fallback()`. Non-streaming chat and rewrite routes do not automatically get the same fallback path. Utility callers may use `llm_call_async_with_fallback()`, and vision uses its own fallback loop.

Model selection has three layers: endpoint resolver hidden-model and first-chat-model selection, `/api/default-chat` per-user default/fallback resolution, and frontend picker auto-selection for empty sessions.

Image routing uses model-name prefixes and `ModelEndpoint.model_type == "image"` to bypass text chat and generate media. Vision analysis uses configured vision models and `vision_model_fallbacks`; image and vision endpoint lifecycle changes should update chat, document processing, Cookbook, and settings UI together.

Provider tool calls are untrusted requests, not authorization. `supports_tools` controls schema emission only; `llm_core` normalizes provider tool-call payloads, while execution authority remains in `src.tool_execution`, `src.tool_security`, and agent-tool policy.

## Degraded And Platform Behavior

- Provider offline or probe failures should surface actionable errors without crashing the app. Async calls retry transient 429/502/503/504 responses before failing.
- Docker deployments may need loopback URL rewriting from `127.0.0.1` to host-accessible addresses.
- Fallback selection must preserve endpoint identity and owner scope. User/API-token LLM dispatch that can carry configured endpoint keys must pass the effective owner into resolver calls.
- Async and streaming calls use dead-host cooldown; sync utility/vision calls do not have identical cooldown coverage.
- llama.cpp slot-affinity routing is local-endpoint behavior only and must not be applied to cloud/provider endpoints.
- Hidden, pinned, cached, endpoint-kind, refresh-policy, and offline model state are UI/runtime compatibility data. Pinned models may not participate in every resolver auto-pick path unless code explicitly includes them.
- SSE/stream parsers tolerate null choice/usage/tool-call entries and null streaming tool-call arguments; provider events should degrade to empty text or shaped stream errors instead of crashing the chat loop.
- Provider adapters carry small model-specific quirks: Opus 4.7+ and official Kimi/Moonshot code payloads omit `temperature`, Kimi/Moonshot reasoning content is preserved separately, ChatGPT Subscription refreshes bearer credentials, and Ollama `/v1` responses for Qwen3/Gemma4-style thinking can suppress thinking text when requested.

## Security Policy

- Endpoint API keys are encrypted in `ModelEndpoint.api_key` and never returned by endpoint APIs; admin surfaces return key presence only.
- Endpoint CRUD, probes, provider discovery, and most endpoint configuration are admin-cookie or internal-tool gated.
- `/api/models` is auth/owner scoped for configured deployments; API-token access requires `chat` scope and token-owner attribution.
- Admin-created model endpoints may target local/LAN servers. Non-admin chat session creation must use registered endpoint IDs. API-token `/api/v1/chat` requires `chat` scope and validates direct `base_url` with public-only URL checks.

## Current Call Sites Include

- chat streaming and non-streaming calls;
- agent loop calls with optional tool schemas;
- compare pane calls;
- research synthesis/probe calls;
- utility model fallbacks for summarization/extraction;
- frontend Settings and model picker endpoint management.

## Current Gaps

- Provider identity, model curation, and frontend logos are split across `llm_core`, `model_routes`, and `providers.js`; there is no canonical provider registry.
- Provider-specific behavior is concentrated in `llm_core.py`, which is large and easy to regress.
- Endpoint identity and fallback behavior need careful review when new OAuth/subscription providers are added.
- Owner must continue to be threaded through new utility/research/default endpoint-resolution call sites so provider keys stay isolated.
- `/api/models` owner-scoped listing/cache behavior, shared/private endpoint dedupe, endpoint-kind refresh policy, fallback-chain owner scope, and image endpoint create/list/update lifecycle need stronger route-level regression coverage.
