# 9router DATA_DIR mount — temporary bridge

Date: 2026-09-11

Credential custody is **provisional** and not settled while Agent Server
or `odysseus-model-jobs` mount 9router private storage.

## TEMPORARY BRIDGE

Current overlay mounts `${APP_DATA_DIR:-./data}/9router` into:

- `openhands-agent-server` at `/opt/odysseus/9router-data`
- `odysseus-model-jobs` at `/opt/odysseus/9router-data`

Those processes mint named virtual keys by writing the 9router sqlite
`apiKeys` table. Odysseus web does not mount that volume and does not hold
a 9router inference credential.

This is **not** the intended long-term interface. 9router `DATA_DIR` is
private storage unless 9router documents it as an integration contract.

## Preferred ownership

```text
Agent Server → supported 9router control/API → scoped virtual key
```

Not:

```text
Agent Server → 9router private storage → mint key
```

The same rule applies to `odysseus-model-jobs`.

## Official control API (present, not overlay-usable today)

Unmodified 9router `0.5.69` exposes `GET`/`POST /api/keys`. That is the
supported key lifecycle API. Dashboard guard treats `/api/keys` as a
protected path: overlay callers need a dashboard JWT, `requireLogin=false`,
or `x-9r-cli-token`. None of those is a documented service-to-service
integration credential for Agent Server.

Odysseus metadata client must keep `/api/keys` off its allowlist. Key mint
is a runtime concern, not a web-process concern.

## Deletion condition

Delete both sqlite mounts and the `_mint_virtual_key` / `mint_jobs_virtual_key`
sqlite writers when **all** of the following are true:

1. Agent Server bootstrap and the model-job worker mint exclusively via
   9router `POST /api/keys` (or another officially documented control API).
2. No service besides 9router mounts `${APP_DATA_DIR}/9router`.
3. Odysseus web still has no 9router inference credential.

Until then, keep the mount labeled TEMPORARY BRIDGE. Do not describe
provider credential custody as settled.
