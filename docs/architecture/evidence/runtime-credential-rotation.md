# Runtime credential rotation (Gate 2)

Date: 2026-09-08 (America/Los_Angeles)  
Probe: `python3 scripts/openhands_probe.py credential-rotation --json`  
Selected branch: `broker`

This is not `direct_rotation`. Live native and ACP conversations kept a stable
identity across `POST /api/conversations/{id}/secrets`, but T2 accept and T1
reject were not observed against a token-validating MCP. OpenCode and Hermes
binaries are absent. ACP secret updates restart the session on the next turn.
A thin in-process MCP broker is the selected delivery mode.

No credentials or raw secret values are recorded here.

## Pins

From `deploy/openhands/versions.env`:

| Component | Pin |
|---|---|
| Agent Server | `ghcr.io/openhands/agent-server:1.45.0-python@sha256:b0104980776ed6adbd6ce406636e08069087b4f11fa130bfd3548b965b262946` |
| Automation | `ghcr.io/openhands/automation:1.11.0` |
| Canvas | `ghcr.io/openhands/agent-canvas:1.16.0@sha256:ab194760cb46098641747b27c1e07458ab1bed439821af7a31c97133ca466bb8` |
| OpenCode | `v1.18.29` |
| Hermes | `v2026.9.7` |

Agent Server OpenAPI `v1.45.0` SHA-256
`937a4bf89a418f043e3d524ef8f660f5a60d3f8dcd131b3464e8532cf95c98c8`.

## Live stack

| Step | Result |
|---|---|
| Local Agent Server image | Present |
| `compose up --no-deps --pull never openhands-agent-server` | Running, healthy, ready |
| `GET /server_info` | `version=1.45.0`, `sdk_version=1.45.0`, capabilities include `credential_binding_v1` |
| Automation GHCR pull | Still `unauthorized`; not required for this gate |
| Canvas image | Present locally; not used |
| `odysseus-mcp` | HTTP liveness only (Task 9). Not a token-validating MCP |
| OpenCode / Hermes binaries | Absent. Image ACP wrappers: `claude-agent-acp`, `codex-acp`, `gemini` |
| Stored agent profiles | `default` / `openhands` only |

Ports remain unpublished. All Agent Server calls used `compose exec`.

## Runtime identities

Probe tokens were dummy `T1`/`T2` labels, never owner credentials.

| Profile | Conversation | Workspace | Persistence dir | Agent kind | Identity stable across rotate |
|---|---|---|---|---|---|
| openhands | `9404b815-077e-4fed-b2a1-bbac24e6c578` | `/workspace` | `workspace/conversations/9404b815077e4fedb2a1bbac24e6c578` | `Agent` | yes |
| opencode | `a66f413e-8bb5-4deb-9df5-82efcd1d7fa4` | `/workspace` | `workspace/conversations/a66f413e8bb54deb9df582efcd1d7fa4` | `ACPAgent` | yes |
| hermes | `0f72f8e3-aef8-4946-8681-eac5bc54b509` | `/workspace` | `workspace/conversations/0f72f8e3aef849468681eac5bc54b509` | `ACPAgent` | yes |

`POST /api/conversations/{id}/secrets` returned HTTP 200 for each conversation.
Conversation GET omitted secret values. `execution_status` stayed `idle`.

ACP conversations were created with `acp_command=[profile]` so a conversation
record existed. The ACP process was not started; `opencode` and `hermes` are
not installed.

## Old-token rejection

Live MCP authorization was unobserved for every profile.

`POST /api/mcp/test` against `http://127.0.0.1:9/mcp` returned HTTP 200 with
`ok=false` and `error_kind=connection`. That is a connection failure, not T2
accept or T1 401.

In-process broker proof: after resolver rotation, the downstream fake MCP
returns 200 for the current token and 401 for the previous token.

## Redaction checks

| Surface | Live Agent Server | In-process broker |
|---|---|---|
| Agent-visible environment | Conversation GET omits secret values; no `ODYSSEUS_DELEGATION` value exported | Token absent |
| Broker logs | Not on the direct path; probe strips request bodies | Token absent |
| Agent Server events | 0 events per probe conversation; T1/T2 absent | Token absent |
| ACP profile snapshots | Only stored profile `default`/`openhands`; no token | Token absent |
| Agent Server container logs | Probe tokens not present in health/access logs | n/a |

## Why not `direct_rotation`

1. T2 accept and T1 401 were not observed against a live MCP.
2. OpenCode and Hermes ACP binaries are absent.
3. `ACPAgent.restart_for_updated_credentials` sets
   `_restart_session_on_next_turn` when the session is already initialized.
4. Native `update_secrets` updates the secret registry for bash env injection
   and does not rebuild live MCP clients.

## Selected branch

`broker`

`McpBroker.forward` keeps a stable local MCP connection and attaches
`DelegationResolver.current_token(execution_id=..., workload_id=...)` on the
server side only. The broker is transport-only.

`probe_credential_rotation()` returns
`ProbeResult(name="credential-rotation", passed=False)` with `mode=broker`.
`passed` is true only for `direct_rotation`.
