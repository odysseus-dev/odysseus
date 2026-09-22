# Venice Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `venice`; paid OpenAI-compatible cloud API represented in
webhook presets and cloud/self-hosted classification tests.

## Shape

Use general identity-only inventory mapping. Treat `api.venice.ai` as a remote API
for routing/security, while keeping model capability per returned model. Do not
infer privacy, tools, reasoning, or context from provider marketing or names.

## Fallback And Current Gaps

Exact `*.venice.ai` preserves provider identity. No verified rich model-card
schema is currently mapped.
