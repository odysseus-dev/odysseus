# GitHub Models Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `github_models`; OpenAI-compatible GitHub Models/Azure
inference endpoint observed in #2995; distinct from GitHub Copilot.

## Shape

Use general identity-only inventory. Deployment IDs and account access
can differ from upstream model IDs. Do not copy Copilot picker metadata,
headers, plan rules, or capabilities into GitHub Models; they are separate
providers despite shared GitHub branding.

## Fallback And Current Gaps

The known `models.inference.ai.azure.com` host selects GitHub Models. Other
Azure deployment hosts require explicit provider configuration. No rich
account-scoped capability catalog is currently mapped.
