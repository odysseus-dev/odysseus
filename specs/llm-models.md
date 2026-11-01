# LLM Models And Endpoints

Last updated: dev@86b886c | 2026-10-15

## Scope

This spec covers model/provider behavior in:

- `src/llm_core.py`;
- `src/endpoint_resolver.py`;
- `src/foreground_model_routing.py`;
- `src/model_discovery.py`;
- `src/model_context.py`, `src/generation_budget.py`, `src/model_pricing.py`, `src/model_profiles.py`, and `src/utility_effort.py`;
- `src/model_capabilities.py`;
- `src/model_capability_readers/`;
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

Kimi Code User-Agent discovery has both sync and async implementations. Async post and stream paths probe `/models` through their existing async client and await each candidate, so header negotiation does not block the event loop; both paths share the accepted-value cache and 403 fallback policy.

Provider-specific behavior is part of this layer: `LLM_CONNECT_TIMEOUT` controls the connect budget for sync and streaming calls, Kimi Code endpoints retry a small whitelisted User-Agent set on 403 and cache the accepted value, official Moonshot/Kimi Code and Anthropic Opus 4.7+ payloads omit sampling controls where required, and major-only Opus IDs such as `claude-opus-5` also omit temperature instead of falling through numeric minor-version parsing. Reasoning models omit or clamp unsupported temperature values, while self-hosted compatible endpoints keep normal parameters unless detected otherwise. Mistral structured content is normalized in async utility calls as well as stream/chat paths, and Mistral/Moonshot/Kimi reasoning content, `gpt-oss` harmony output, DeepSeek V4 thinking identifiers, and native/OpenAI-compatible Ollama thinking formats keep hidden reasoning separate from visible text. Tool names that collide with GPT-OSS built-ins are aliased on the provider boundary and mapped back before execution. Copilot request metadata remains defensive against malformed `request_flags`.

## Canonical Provider And Model Shape

`src.model_capabilities` owns canonical model family, task, modality, capability, limit, evidence, assertion, deterministic-control, probe-result, reasoning-control token, and display-query values. `src.model_capability_readers` owns endpoint-scoped stable identity, lightweight provider detection, record serialization, and normalization of already-fetched provider payloads. Readers do no network I/O. Model-specific observations are kept in `model-quirks.md`, not a runtime registry without a consumer.

Provider support and model support are different facts. A provider may expose tools, reasoning, vision, or multiple APIs while individual models differ. Provider-native readers describe where model evidence can appear. Current concrete readers cover generic OpenAI-compatible identity, OpenAI, OpenRouter, Google, Ollama, LM Studio, and llama.cpp. Identity-only model lists remain unknown.

Reader dispatch uses an explicit vendor first, then endpoint kind, label-bounded hostname suffix, and common local-port hints. Generic payload handling accepts `data[]` or `models[]` items with `id`, `name`, or `model`; it does not accept a bare list and never promotes capability-looking fields. Unknown fields remain in the in-memory raw record. See [model-capability-canonical.md](model-capability-canonical.md), [model-quirks.md](model-quirks.md), and the [provider map](model-providers/_readme.md).

Canonical records are exercised by focused unit tests but are not wired into runtime discovery, endpoint resolution, model context, request shaping, or frontend pickers. The reader package’s `detect_vendor()` helper does have narrow runtime consumers in `src/ai_interaction.py` and gallery routes for OpenRouter image transport; that does not integrate canonical capability records. `routes/model_routes.py` model probes continue to return model IDs through their existing runtime path.

Route-level probe helpers in `routes/model_routes.py` are the current exception: they build minimal provider-specific probe payloads using `llm_core` detection helpers. Keep probe behavior aligned with `llm_core` provider adapters. LLM provider HTTP clients and endpoint probes share `src.tls_overrides.llm_verify()`, which can add an operator-provided `LLM_CA_BUNDLE` on top of normal certificate verification without turning verification off or widening that trust to arbitrary URL fetches.

## Endpoint Resolution

`src.endpoint_resolver` owns endpoint normalization and URL construction:

- base URL normalization;
- chat and model-list URL construction;
- endpoint ID resolution;
- chat, utility, and vision fallback candidate selection;
- Tailscale hostname resolution where available.

OpenAI-compatible model-list URL construction preserves `/v1` bases and inserts `/v1/models` for bare local bases such as LM Studio `http://localhost:1234`.

`routes/model_routes.py` owns model endpoint CRUD, admin provider discovery/probing, visible/hidden/pinned model lists, endpoint kind and refresh policy, curated/extra model partitioning, `/api/models` catalog caching, Docker loopback rewriting, tool-support probing, provider-auth linkage, endpoint-dependent settings cleanup, and owner filtering. Endpoint dedupe allows the same base URL under different API keys and surfaces API-key fingerprints/key presence without returning secrets.

`routes/session_routes.py` owns binding sessions to endpoint IDs, owner-scoped header construction, raw-endpoint rejection for non-admin users, model validation, and persisted session headers. Compare panes and normal chat session creation use this path.

`ModelEndpoint` rows own API keys, base URLs, cached/hidden/pinned models, model type, endpoint kind, refresh mode/interval/timeout, supports-tools state, per-model tool-mode overrides, nullable owner, optional provider-auth linkage, and provider metadata. `owner = NULL` means legacy/shared; non-null rows are private to that owner, while ordinary admin inventory can see all. ChatGPT Subscription rows are an exception: account visibility requires exact effective-owner equality even for admins. Secret fields must remain encrypted and scrubbed in responses.

Decrypted endpoint headers can be copied into session metadata for chat use. Endpoint deletion must clear dependent settings and copied session headers.

## Model Discovery And Lists

`src.model_discovery` owns host/env/Tailscale/local-port scanning for model servers. Admin `/api/providers` and `/api/discover` use that scanner; endpoint CRUD, test, refresh, and hidden-model controls are frontend-owned by `static/js/admin.js`.

`/api/models` is the normal picker/catalog surface. It is auth/owner scoped, per-user/admin-flag cached briefly, can trigger background refresh, preserves offline endpoint rows, filters hidden models, and preserves pinned model IDs for UI selection. API-token callers must carry `chat` scope and a token owner before they can list models. API/proxy endpoint inventory is visible by default until an explicit `pinned_models` allow-list is saved; an explicit empty list means show none, and legacy hidden-list state is upgraded to the equivalent pins so endpoint settings, picker checkboxes, and chat agree. Proxy/API endpoints can be marked cached-first/manual so large upstream catalogs are not repeatedly probed, while explicit refresh paths use longer manual timeouts. Local endpoints get cheap reachability probes before expensive refreshes where possible, and endpoint responses can include explicit `supports_tools` state for schema-emission heuristics. Google Gemini API endpoints use the native paginated `generativelanguage.googleapis.com/v1beta/models` catalog, send API keys in `x-goog-api-key`, retain only content-generation model IDs, and default to manual refresh unless the caller explicitly chooses another mode. Probe failure returns no curated Google fallback. `static/js/models.js` and `static/js/modelPicker.js` own the sidebar/picker catalog; `static/js/model/matchKey.js` owns longest-substring model-info/pricing key matching; `static/js/settings.js` coordinates default, utility, vision and fallback selectors; `static/js/settings/imageModels.js` and `static/js/settings/speech.js` own their extracted image and TTS/STT panels.

`src.task_endpoint` owns background-task endpoint/model resolution for task routes and scheduler callers. It resolves `task_endpoint_id`/`task_model` through the normal endpoint resolver with owner context.

Cookbook and HWFit own local model download, serve, ranking, and auto-registration flows. They can create LLM or image `ModelEndpoint` rows, but provider dispatch remains owned by `llm_core`/endpoint resolution.

## Context Length

`src.model_context` owns model context-length lookup/query and token estimation. Cache keys include endpoint plus model so identical model names on different endpoints do not bleed context-window data. Local llama.cpp context probes try `/slots` and then `/props.default_generation_settings.n_ctx`. Unknown proxy/API models can pick up real context windows from endpoint catalog metadata such as `context_length`; otherwise unknown lengths stay explicit unknowns rather than default values. Known lengths feed chat/agent token-budget scaling through `src.context_budget`. Token estimation counts assistant `tool_calls` arguments so compaction sees tool-only turns instead of underestimating them. Chat/agent context budgeting should call this layer instead of hardcoding model windows.

## Runtime Fallback And Routing

`src.foreground_model_routing` owns foreground Chat/Agent fallback policy. Selected models are strict by default. Fallback requires owner-scoped `foreground_fallback_enabled=true` and an ordered `foreground_model_fallbacks` list; the old `default_model_fallbacks` setting is retired, ignored, and not migrated into consent. Named users never inherit a legacy flat/single-user fallback choice, candidate lists are capped at ten exact owner-visible models, and caller-provided allowed-model restrictions remain authoritative.

Only eligible availability failures before substantive output can fall through. Default eligible statuses are 408, 425, 429, 500, 502, 503, 504, 507, 508, and 529. Missing endpoint/configuration, provider/schema/request errors, empty completions, and post-content failures do not silently change routes. A candidate commits after non-empty visible/reasoning text or a tool call; the answering route is then pinned. Foreground routing carries model and endpoint descriptors together, shapes context/compaction route-neutrally across candidates, persists only answering-route compaction, and records requested/actual/per-round route provenance plus cost attribution. Utility/background and vision fallbacks remain separate policies.

Model selection has three layers: endpoint resolver hidden-model and first-chat-model selection, `/api/default-chat` per-user default/fallback resolution, and frontend picker auto-selection for empty sessions.

Image routing uses model-name prefixes and `ModelEndpoint.model_type == "image"` to bypass text chat and generate media. Vision analysis uses configured vision models and `vision_model_fallbacks`; image and vision endpoint lifecycle changes should update chat, document processing, Cookbook, and settings UI together.

Provider tool calls are untrusted requests, not authorization. `supports_tools` controls schema emission only; `llm_core` normalizes provider tool-call payloads, while execution authority remains in `src.tool_execution`, `src.tool_security`, and agent-tool policy.

## Degraded And Platform Behavior

- Provider offline or probe failures should surface actionable errors without crashing the app. Async calls retry transient 429/502/503/504 responses before failing.
- Docker deployments may need loopback URL rewriting from `127.0.0.1` to host-accessible addresses.
- Foreground fallback selection must preserve endpoint identity, explicit owner consent, allowed-model policy, and owner scope. User/API-token LLM dispatch that can carry configured endpoint keys must pass the effective owner into resolver calls.
- Async and streaming calls use dead-host cooldown; sync utility/vision calls do not have identical cooldown coverage.
- llama.cpp slot-affinity routing is local-endpoint behavior only and must not be applied to cloud/provider endpoints.
- Hidden, pinned, cached, endpoint-kind, refresh-policy, and offline model state are UI/runtime compatibility data. Pinned models may not participate in every resolver auto-pick path unless code explicitly includes them.
- SSE/stream parsers tolerate null choice/usage/tool-call entries and null streaming tool-call arguments; provider events should degrade to empty text or shaped stream errors instead of crashing the chat loop.
- Provider adapters carry small model-specific quirks: Opus 4.7+ and official Kimi/Moonshot code payloads omit `temperature`, Kimi/Moonshot/Mistral reasoning content is preserved separately, ChatGPT Subscription refreshes bearer credentials, native Ollama can handle multimodal content, and Ollama `/v1` responses for Qwen3/Gemma4-style thinking can suppress thinking text when requested.

## Additional Runtime Owners

`src.generation_budget` estimates native tool-schema and image reserve costs, fits output limits to the remaining context budget, and parses provider context errors for bounded recovery. `llm_core` uses these helpers in async/streaming paths; recovery is separate from switching to a fallback model. `src.model_context.estimate_text_tokens()` counts non-ASCII characters individually and Latin characters at roughly 0.3 tokens each, including tool-call names/arguments. These remain estimates, not tokenizer guarantees.

`src.model_profiles` selects the Odysseus compact schema and progressive-thinking profile from bounded `odysseus`/`ajax` tokens in the model ID leaf. `supports_user_thinking_toggle()` uses explicit model-family patterns and suppresses the toggle for that profile and GPT/o-series leaves; these are runtime heuristics outside the canonical reader. Endpoint `model_tool_modes` accepts `none`, `compact`, or `full` per model, with legacy aliases normalized by model routes. This is schema/routing configuration, not tool execution authority.

`src.utility_effort` resolves owner-aware utility effort, including fallback-specific overrides and eligible session-route effort. Validation currently uses ChatGPT model metadata, including maintained defaults and model-name fallback metadata; it is not evidence from the canonical reader. `src.model_pricing` owns server-side known-price estimates; unknown models return no estimate and local/subscription endpoints are excluded from per-token cost tracking.

Featherless endpoints use an explicit pin inventory instead of bulk catalog refresh. Admin `GET /api/model-endpoints/{ep_id}/catalog-search` searches active conversational models available on the current plan with bounded pagination and a 45-second in-memory query cache. New Featherless rows default to an empty pin list. `/api/models?refresh=true&wait_refresh=true` can wait up to 15 seconds on the shared background refresh.

`src.endpoint_resolver.resolve_owner_registered_endpoint()` accepts only an enabled owner-visible registered base or its derived chat URL, returning the server-owned row. URL identity includes scheme, host, effective port, and path; caller credentials, query strings and fragments are rejected. `same_endpoint_base()` governs exact origin/base-path credential reuse. Native runtimes can rewrite persisted `host.docker.internal` bases to loopback.

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

## Discovery And Route Identity Details

`src.model_discovery` timestamps successful Tailscale host queries, including an empty result, for the 60-second cache. A failed query does not become a successful empty observation. This discovery cache is separate from endpoint URL resolution and model-context probe caches.

Exact registered endpoint authority rejects URL credentials, parameters, and any query or fragment delimiter, including an empty one; it accepts the registered base or an explicitly derived inference route rather than broad same-host aliases. Owner-visible shared endpoint rows remain distinct from exact-owner-only data. Utility effort forwarding remains explicit at participating callers; a configured off/none/default selection can suppress session effort rather than being treated as a missing override.

## Current Gaps

- Runtime provider detection, model curation, and frontend logos are still split across `llm_core`, `model_routes`, and `providers.js`; canonical records have no runtime model-catalog consumer yet, while provider detection is reused by media paths.
- Provider-specific behavior is concentrated in `llm_core.py`, which is large and easy to regress.
- Several runtime request builders still use model-name heuristics. They should migrate only after endpoint/provider code supplies structured identity and a real consumer contract; the canonical catalog does not add a parallel quirk matcher.
- Endpoint identity and fallback behavior need careful review when new OAuth/subscription providers are added.
- Owner must continue to be threaded through new utility/research/default endpoint-resolution call sites so provider keys stay isolated.
- `/api/models` owner-scoped listing/cache behavior, shared/private endpoint dedupe, endpoint-kind refresh policy, fallback-chain owner scope, and image endpoint create/list/update lifecycle need stronger route-level regression coverage.
