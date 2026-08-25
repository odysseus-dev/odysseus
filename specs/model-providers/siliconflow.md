# SiliconFlow Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `siliconflow`; global/CN OpenAI-compatible provider
proposed in #5562.

## Shape

Use the general `/v1/models` identity-only inventory reader for both regional
surfaces. Region/base URL and API key remain endpoint identity. A regional
provider-native schema is required before any item fields are promoted; model
tokens in returned IDs or PR examples are never capability evidence.

## Fallback And Current Gaps

Exact SiliconFlow hosts or explicit kind preserve provider identity. The open
provider work has no confirmed rich capability card; regional path/host details
and current payload fixtures need revalidation before runtime integration.
