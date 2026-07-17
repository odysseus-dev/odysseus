# GitHub Copilot Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `copilot`; OpenAI-compatible chat with Copilot headers
and OAuth; reader `src/model_capability_readers/copilot.py`; runtime adapter
`src/copilot.py` and routes in `routes/copilot_routes.py`.

## Catalog Shape

The observed Copilot `/models` response uses `data[]` entries with:

- `id`;
- `model_picker_enabled`;
- `capabilities.supports.tool_calls` and `.vision`;
- optional limit/family metadata.

Picker-enabled entries are selectable chat models. Nested true support fields
claim only that model's tool/vision capability. Picker-disabled utility entries
remain identity-only unless their own structured fields say more. If no entry
advertises picker state, current adapter behavior can retain all identities as
a compatibility fallback, but that does not promote capability.

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
- Account/plan/policy availability must remain endpoint-user scoped.
