# ACP–MCP contract (Gate 3)

Date: 2026-09-08 (America/Los_Angeles)  
Probe: `python3 scripts/openhands_probe.py acp-mcp --json`  
Selected branch: `proxy`  
Credential delivery (Task 3): `broker`

Direct OpenCode/Hermes MCP forwarding is not proven on the pinned stack.
A narrow `AcpMcpProxy` translates ACP transport onto the Task-3 broker.
The proxy does not call Odysseus REST, store durable tokens, or implement
domain rules. Native-agent behavior is not recorded as ACP capability.

No credentials or raw secret values are recorded here.

## Pins

From `deploy/openhands/versions.env`:

| Component | Pin |
|---|---|
| Agent Server | `ghcr.io/openhands/agent-server:1.45.0-python@sha256:b0104980776ed6adbd6ce406636e08069087b4f11fa130bfd3548b965b262946` |
| OpenCode | `v1.18.29` |
| Hermes | `v2026.9.7` |

## Live ACP

| Check | Result |
|---|---|
| `opencode` / `hermes` binaries | Absent |
| Agent Server ACP wrappers | `claude-agent-acp`, `codex-acp`, `gemini` |
| Stored profiles | `default` / `openhands` only |
| Direct scoped MCP (`notes.read` allow, `mail.send` deny) | Unsupported |
| Direct reconnect / resume / cancel | Unsupported |

## Capability matrix

| Profile | Forwarding | Reconnect | Resume | Cancellation | Secret handling |
|---|---|---|---|---|---|
| opencode | proxy | proxy | proxy | proxy | redacted |
| hermes | proxy | proxy | proxy | proxy | redacted |

In-process proxy proof: scoped `notes.read` is forwarded through
`McpBroker`; `mail.send` is denied before transport; reconnect restores
broker context; cancel stops forwarding; Authorization headers from ACP
config are not persisted in snapshots.

## Selected branch

`proxy`

`probe_acp_mcp()` returns `ProbeResult(name="acp-mcp", passed=False)` with
`proxy_required=true`. `passed` is true only for `direct`.
