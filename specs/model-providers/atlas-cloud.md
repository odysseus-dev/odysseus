# Atlas Cloud Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `atlas_cloud`; OpenAI-compatible provider proposed in
#5566 with live `/v1/models` observations for current Qwen/DeepSeek offerings.

## Shape

Treat the observed list as identity-only. Even capability-looking item fields
remain raw until an Atlas-specific discriminating shape intentionally maps
them. The model IDs observed by a PR demonstrate availability at that time,
not permanent capability or a reason to hardcode family-name behavior.

## Fallback And Current Gaps

Exact Atlas Cloud host or explicit kind preserves identity; otherwise use the
inventory fallback. The provider work is open/unmerged and has no independently
versioned rich catalog schema, so evidence remains provisional.
