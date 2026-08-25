# ChatGPT Subscription Provider Shape

Last updated: PR #5849 | 2026-08-25

## Scope

Canonical provider ID `chatgpt_subscription`; Codex Responses transport;
auth and runtime code in `src/chatgpt_subscription.py`,
`routes/chatgpt_subscription_routes.py`, and `src/llm_core.py`.
A dedicated `chatgpt_subscription` reader normalizes the native account
catalog into endpoint-scoped canonical records.

## Catalog Shape

The account-scoped Codex models endpoint returns root `models[]`; `slug` is the
request identity and `visibility`/`priority` control availability/order. These
fields do not prove tools, reasoning, vision, or context. The catalog's
`supported_reasoning_levels`, `default_reasoning_level`, `support_verbosity`,
and `default_verbosity` fields are explicit per-model control evidence.
Null/malformed model lists fail soft rather than crashing discovery
(#5280/#5281).

The dedicated reader keeps the general capability card unknown while mapping
only those explicit native control fields into `DeterministicControl` values.
Records are cached with the endpoint catalog and exposed to the picker; both
the browser and Responses payload builder reject values absent from that same
record. Model names, prefixes, and endpoint URLs do not enable controls.

## Request And Response Shape

Transport uses a ChatGPT backend Responses endpoint, `input` items, flattened
function tools, streamed function-call argument events, exact `call_id`, and
`function_call_output` continuation. Parallel calls and encrypted reasoning
continuity require preserving typed output/history rather than coercing all
roles to text. This shape is supported by the existing adapter and the focused
tool-calling follow-up evidence in #5490; unmerged observations remain claimed
until integrated/reproduced.

OAuth/device credentials and refresh are provider-session behavior. Expired
credentials should return an actionable reconnect error, not generic model
failure.

## Fallback And Safety

Only the explicit internal base/ChatGPT host selects this provider. Never send
subscription credentials to a custom OpenAI-compatible URL. Catalog slugs stay
identity-only unless account-scoped fields or probes supply capability.

## Current Gaps

- Comprehensive Responses tool/reasoning parity is still evolving.
- The account catalog does not currently provide a complete canonical
  capability card for every slug.
