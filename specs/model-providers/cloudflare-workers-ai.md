# Cloudflare Workers AI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `cloudflare_workers_ai`; OpenAI-compatible Workers AI
endpoint observations in #5175; explicit provider configuration required.

## Shape

Cloudflare account/path identity is part of the endpoint. Use the general
OpenAI-compatible inventory reader for returned model cards, preserving full
model IDs but no capability fields.
Do not identify the provider from broad `api.cloudflare.com` alone or infer
capability from Workers AI catalog prose.

## Fallback And Current Gaps

Provider identity must be explicit until a narrow account/AI path matcher is
implemented. There is no rich normalized capability catalog reader.
