# ChatGPT Subscription Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `chatgpt_subscription`; Codex Responses transport;
auth and runtime code in `src/chatgpt_subscription.py`,
`routes/chatgpt_subscription_routes.py`, and `src/llm_core.py`.
There is no dedicated ChatGPT Subscription canonical reader on current `dev`.

## Catalog Shape

The account-scoped Codex models endpoint returns root `models[]`; `slug` is the
request identity and `visibility`/`priority` control availability/order. These
fields do not prove tools, reasoning, vision, or context. Null/malformed model
lists fail soft rather than crashing discovery (#5280/#5281).

The canonical generic reader does not accept `slug`-only items, so this runtime
catalog is not currently normalized into `ModelCapabilityRecord` values.

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
- Account model slugs are not consumed by the canonical reader package.
- The account catalog does not currently provide a complete canonical
  capability card for every slug.
