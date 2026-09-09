# OpenHands stack operations

## Startup

```bash
docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait
python3 scripts/openhands_probe.py stack --json
python3 scripts/openhands_probe.py acceptance --json
```

Agent Server and Automation ports stay unpublished. Use `docker compose exec` for in-network checks. Canvas binds to `OPENHANDS_CANVAS_BIND` / `OPENHANDS_CANVAS_PORT`.

`odysseus-mcp` serves Streamable HTTP MCP from `python -m mcp_servers.odysseus_server` (`/health` and `/api/health`).

## Selected Phase-1 branches

| Gate | Branch |
|---|---|
| Interactive execution | Agent Server `continued_run` |
| Credentials | MCP broker |
| ACP MCP | `AcpMcpProxy` |
| ApprovalGrant | Odysseus-signed; OpenHands never holds the signing key |

## Health and diagnosis

- Agent Server: `curl` `/health` inside the container
- Probe CLIs: `stack`, `automation-existing-conversation`, `credential-rotation`, `acp-mcp`, `confirmation-approval-grant`, `acceptance`
- Projection: rebuild from Agent Server events; unknown kinds quarantine and set `degraded`

## Token and key rotation

HMAC material is derived from `src.secret_storage.hmac_secret`. Rotate by replacing `data/.app_key` and reissuing delegations. Agents cannot reissue tokens.

## Sandbox and rollback

Per-run sandboxes are Automation-owned. Rollback unit is the previous Odysseus release plus the pinned compose images in `deploy/openhands/versions.env`. Do not delete `src/agent_loop.py` until live acceptance (`probe acceptance` `live_stack: true`) and the disposition ledger mark residuals replaced.

## Canvas

Native UI uses “Open in Agent Canvas” as a URL. Canvas is not embedded.
