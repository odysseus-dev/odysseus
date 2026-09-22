# AWS Bedrock Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `bedrock`; UI/provider mapping currently recognizes AWS
Bedrock, but the canonical layer has no native Bedrock runtime reader.

## Shape

Bedrock is not generally an OpenAI-compatible host: model IDs, inference
profiles, request/response unions, signing, and per-family payloads differ.
Only an explicitly configured OpenAI/Anthropic-compatible gateway may use those
dialects. Native Bedrock capability must come from a versioned Bedrock model
catalog plus exact foundation-model/inference-profile identity.

## Fallback And Current Gaps

Do not classify all `amazonaws.com` hosts as Bedrock; use explicit kind or a
future region-aware exact host/path shape. General fallback is safe only behind
an explicitly compatible gateway. Native signing, catalogs, and family payload
mappings remain unimplemented.
