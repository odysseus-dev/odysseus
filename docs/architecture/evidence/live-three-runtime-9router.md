# Live three-runtime 9router acceptance

Date: 2026-09-11

Observed on the overlay stack after pinning OpenCode `v1.18.29` and Hermes
`v2026.9.7` into `data/openhands-runtime-bin` (`/opt/oh-bin`).

Token-successful completions are still pending a connected 9router upstream
provider. Direct-provider fallback was not observed.

## Native OpenHands

Agent Server settings remain `http://9router:20128/v1`, `auth_type=api_key`,
`api_mode=chat`, LiteLLM prefix `openai/`. Completions POST to that base.
9router answers with a provider-credential miss when no upstream is connected.

## OpenCode ACP

`/opt/oh-bin/opencode run` loaded `/home/opencode/.config/opencode/opencode.json`
and selected `llm.provider=ninerouter` / `llm.model=kr/claude-opus-5`.
9router returned `No active credentials for provider: kiro`.

## Hermes ACP

`/opt/oh-bin/hermes -z` with `HERMES_HOME=/home/hermes/.hermes` returned
`HTTP 404: No active credentials for provider: kiro`.

## Not claimed

- R7 is not fully satisfied: no successful model tokens from a connected
  upstream.
- ACP MCP contract tests remain xfail until live scoped MCP is proven.
- Hermes stays off the Odysseus chooser.
- 9router `DATA_DIR` sqlite minting remains a temporary bridge.
- Task 18 remains held.
