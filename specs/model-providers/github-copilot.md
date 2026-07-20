# GitHub Copilot Provider Shape

Last updated: dev@e57f60b | 2026-07-20

## Scope

Canonical provider ID `copilot`; OpenAI-compatible chat with Copilot headers
and OAuth; runtime adapter `src/copilot.py` and routes in
`routes/copilot_routes.py`. There is no dedicated Copilot canonical reader on
current `dev`.

## Catalog Shape

The observed Copilot `/models` response uses `data[]` entries with:

- `id`;
- `model_picker_enabled`;
- `capabilities.supports.tool_calls` and `.vision`;
- optional limit/family metadata.

Runtime model discovery uses picker state for availability. The canonical
reader package does not map the nested support fields; an explicitly supplied
`copilot` vendor currently uses generic identity-only normalization, and
`model_picker_enabled` does not become canonical capability.

## Request And Response Shape

Chat is OpenAI-compatible but requires Copilot/GitHub API version, editor/plugin
identity, intent, integration, and initiator headers; image requests add the
vision request flag. Header derivation must tolerate malformed message entries.
OAuth token exchange and access policies are provider authentication, not model
capability.

## Fallback And Safety

Use exact GitHub Copilot host or explicit kind, including the constrained
enterprise `copilot-api.*.ghe.com` form. Do not treat arbitrary `ghe.com` hosts
as Copilot. Official model availability tables are useful registry context but
do not replace the account-scoped catalog response.

## Current Gaps

- The catalog shape is implementation-observed and needs ongoing fixture
  comparison with current Copilot clients.
- Copilot catalog capability fields are not normalized by current `dev`.
- Account/plan/policy availability must remain endpoint-user scoped.
