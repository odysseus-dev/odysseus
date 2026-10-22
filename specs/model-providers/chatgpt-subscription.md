# ChatGPT Subscription Provider Shape

Last updated: dev@86b886c | 2026-10-15

## Scope

Canonical provider ID `chatgpt_subscription`; Codex Responses transport; auth and runtime code in `src/chatgpt_subscription.py`, `routes/chatgpt_subscription_routes.py`, and `src/llm_core.py`. There is no dedicated ChatGPT Subscription canonical reader on current `dev`.

## Catalog Shape

The account-scoped Codex models endpoint returns root `models[]`; `slug` is the request identity and `visibility`/`priority` control availability/order. These fields do not prove tools, reasoning, vision, or context. Null/malformed model lists fail soft rather than crashing discovery (#5280/#5281).

The canonical generic reader does not accept `slug`-only items, so this runtime catalog is not currently normalized into `ModelCapabilityRecord` values.

## Request And Response Shape

Transport uses an inference-only ChatGPT backend Responses payload allowlist: model, instructions, input, stream, store, optional temperature and reasoning effort. It drops native tools, tool choice, previous-response/background fields and unrelated options. `build_responses_input()` flattens conversation history into text input/output items; multipart image blocks are not preserved. Native tool calls/results and encrypted typed reasoning continuity are not preserved as a general protocol. Agent tools therefore use the text protocol on this provider rather than a claimed native function-call continuation.

OAuth/device credentials and refresh are provider-session behavior. Expired credentials should return an actionable reconnect error, not generic model failure.

## Fallback And Safety

Only the explicit internal base/ChatGPT host selects this provider. Never send subscription credentials to a custom OpenAI-compatible URL. Catalog slugs alone remain identity-only. Runtime catalog metadata can retain advertised reasoning levels, but the runtime also supplies maintained defaults and model-name fallback metadata; those are not canonical verified capability cards.

## Account And Usage Ownership

Device-flow setup supports multiple accounts using stable provider-auth IDs; display labels are not identity. Reconnect, delete, inventory and account usage are owner-scoped. Subscription endpoint inventory applies exact effective-owner equality even for admins, and refresh deduplication includes the provider-auth ID to keep accounts separate.

`GET /api/chatgpt-subscription/accounts/{auth_id}/usage` returns normalized rate-limit telemetry for exactly one owned auth session. `src.chatgpt_subscription.UsageCache` keeps deep-copied normalized snapshots for 45 seconds, bounded to 256 auth-id entries with FIFO eviction rather than single-flight refresh, and ownership is checked before cache hits. Forced refresh replaces the snapshot; reconnect/deletion invalidates cached usage. Errors return credential-free unavailable payloads. `static/js/chatgptSubscriptionUsage.js` owns the frontend read/display flow.

Runtime catalog metadata retains supported/default reasoning levels and is exposed through `/api/models` model metadata. `validate_reasoning_effort()` omits unsupported overrides; its maintained defaults and name-based fallback are runtime compatibility rules rather than canonical provider evidence.

## Binding And Telemetry Limits

New account setup creates a distinct auth/endpoint pair. Sessions persist endpoint id so the bound endpoint wins over labels; removed or disabled bound endpoints fail closed rather than borrowing another login. Legacy unbound sessions can bind the oldest enabled owner-visible URL-matching endpoint before resolving its exact owner-scoped auth session. Runtime bearer credentials are attached request-locally only to the registered subscription route. Forked session account identity and usage cache ownership remain independent of chat approval grants.

Usage parsing bounds finite numeric telemetry, preserves missing values as unknown and shapes credential-free errors; a JWT claim can supply unverified routing metadata without becoming verified identity. Usage cache and optimistic UI expansion do not guarantee fresh quota or live provider acceptance. Session `effort:<level>` is validated on model changes/partial generation settings, and reasoning effort partitions inference cache identity; metadata defaults/name fallbacks remain compatibility evidence.

## Current Gaps

- Comprehensive Responses tool/reasoning parity is still evolving.
- Account model slugs are not consumed by the canonical reader package.
- The account catalog does not currently provide a complete canonical
  capability card for every slug.
