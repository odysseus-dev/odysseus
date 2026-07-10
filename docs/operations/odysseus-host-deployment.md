# Odysseus host deployment for Misumi

## Boundary

Odysseus is the authenticated runtime/control plane. Misumi is the household-facing interface. The household repository remains canonical and read-only during Phase A.

Default deployment:

- host: `DESKTOP-IN7O23D`;
- app: `0.0.0.0:420`, reachable only through a LAN-scoped firewall rule;
- auth: enabled, with localhost bypass disabled;
- source: a clean checkout of `tyecam1/odysseus`;
- state: `%LOCALAPPDATA%\Odysseus\Misumi`, outside the source checkout;
- household root: `MISUMI_HOUSEHOLD_ROOT=C:\Users\User\Documents\flat-knowledgebase`.

Do not reuse a dirty source checkout as the deployment base. Build side-by-side, validate on a non-production port, then change the scheduled task.

## Host-local environment

Store configuration outside Git. The lifecycle script always forces `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, and `MISUMI_REQUIRED=true`.

```powershell
$env:MISUMI_HOUSEHOLD_ROOT = 'C:\Users\User\Documents\flat-knowledgebase'
$env:MISUMI_MODEL_HEALTH_URL = 'http://127.0.0.1:11434/api/tags'
$env:MISUMI_INTERFACE_HEALTH_URL = 'http://192.168.4.37:8770/health'
```

Create a narrowly scoped API token for the interface bridge. Keep it in the interface-box process environment as `ODYSSEUS_API_TOKEN`; never put it in `config.json` or Git.

## Lifecycle commands

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Install -InstallFirewall
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Start
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Stop
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Restart
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Status
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Health
powershell -ExecutionPolicy Bypass -File .\scripts\windows\odysseus-host.ps1 -Action Logs -Tail 120
```

`Health` checks unauthenticated liveness. It checks authenticated readiness when `ODYSSEUS_API_TOKEN` is present. The token is neither printed nor persisted.

## Readiness contract

`GET /api/health` proves only that the process can answer. `GET /api/ready` reports database and data-directory integrity, auth versus bind safety, household reachability, skill and scheduler availability, vector state, model health, and optional interface health.

When `MISUMI_REQUIRED=true`, household, skills, scheduler, and model checks are critical. A degraded critical check returns HTTP 503.

## Side-by-side cutover

1. Preserve the existing checkout and staged diff.
2. Install the integration checkout and virtual environment at a new path.
3. Run it on port 1420 with an isolated data directory.
4. Verify liveness, authenticated readiness, generic chat, and every `/misumi/*` smoke test.
5. Stop the test instance.
6. Install the reviewed checkout's scheduled task on port 420.
7. Confirm one listener, then point the interface box at `http://DESKTOP-IN7O23D:420/misumi`.
8. Keep the reference agent on port 4500 as rollback until the read-only eval suite passes.

Rollback restores the previous scheduled task and interface `agentUrl`. Household files are not involved.
