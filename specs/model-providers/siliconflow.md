# SiliconFlow Provider Shape

Last updated: dev@28d27ee | 2026-07-17

## Scope

Canonical provider ID `siliconflow`; global/CN OpenAI-compatible provider
proposed in #5562.

## Shape

Use the general `/v1/models` identity/structural reader for both regional
surfaces. Region/base URL and API key remain endpoint identity. Promote only
explicit fields, not model-family tokens in returned IDs or PR examples.

## Fallback And Current Gaps

Exact SiliconFlow hosts or explicit kind preserve provider identity. The open
provider work has no confirmed rich capability card; regional path/host details
and current payload fixtures need revalidation before runtime integration.
