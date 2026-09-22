# Azure OpenAI Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `azure_openai`; Azure deployment-scoped OpenAI dialects;
custom endpoints use explicit configuration.

## Shape

Azure commonly identifies deployments rather than globally stable model IDs.
Preserve endpoint, deployment ID, API version, and underlying model/version as
separate structured identity when returned. A standard OpenAI-compatible model
list is identity-only until an Azure-specific reader intentionally maps its
deployment fields.

Request paths and authentication can be deployment/API-version specific; do
not blindly append public OpenAI paths or copy provider quirks. Capability and
limits are deployment scoped.

## Fallback And Current Gaps

Known `*.openai.azure.com` hosts select Azure OpenAI; other Azure gateways need
explicit kind. Odysseus lacks a native Azure deployment catalog reader and
structured API-version persistence in the canonical record.
