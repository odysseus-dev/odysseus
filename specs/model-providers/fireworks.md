# Fireworks AI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `fireworks`; OpenAI-compatible cloud transport with path
prefixes such as `/inference/v1`; curation and URL handling in
`routes/model_routes.py` and `src/endpoint_resolver.py`.

## Shape

Use the general identity-only inventory reader. Fireworks IDs can contain
account/model paths; preserve the full request ID and endpoint scope. Item
modalities, supported parameters, task/type, and limits require a
Fireworks-native mapped shape before promotion.

## Fallback And Current Gaps

Exact `*.fireworks.ai` preserves provider identity and its configured path
prefix. Do not normalize account-qualified IDs by taking the last path segment.
No verified rich Fireworks capability catalog is currently mapped.
