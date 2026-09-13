# Penpot on ZimaOS

`docker-compose.zimaos.yml` is the ZimaOS/CasaOS-format stack for the Penpot
instance Odysseus designs are exported to (`http://homelab.nas:9001`).

## Why exports failed

Exporting (PNG/SVG/PDF) goes browser -> frontend nginx `/api/export` ->
`penpot-exporter:6061`, whose headless browser then loads the app again from
the frontend to render it. Two things in the ZimaOS export of the stack broke
that chain:

1. **No `PENPOT_SECRET_KEY` on the exporter.** Penpot 2.x's exporter config
   schema requires it (it derives the export-token key from it), so the
   container never came up and nginx returned `502 Bad Gateway` for every
   export.
2. **Wrong port for the internal frontend address.** The exporter was told
   `PENPOT_PUBLIC_URI=http://big-bear-penpot-frontend:9001`. Port 9001 only
   exists on the NAS; inside the Docker network the frontend listens on 8080.
   `PENPOT_INTERNAL_URI=http://penpot-frontend:8080` now names it explicitly.

The backend's public URI (used for links and cookies) and the frontend's
flags were also missing or internal-only; they are set to the real user-facing
URL now.

## Installing / updating on ZimaOS

1. Fill in the two placeholders (`__PENPOT_SECRET_KEY__` on backend and
   exporter, `__PENPOT_DB_PASSWORD__` on backend and postgres). The DB
   password must be the one the existing `pgdata` was created with; the
   secret key can be anything, as long as backend and exporter agree.
2. ZimaOS -> App Store -> **Install a customized app** -> **Import** (top
   right) -> paste the file.
3. Keep the app name `penpot-backend` so it replaces the existing app and
   reuses `/DATA/AppData/big-bear-penpot/{assets,pgdata}`.
4. Submit. Postgres and Redis come up first (healthchecks), then backend,
   exporter, frontend.

## Verifying

```bash
# 502 = exporter still down. 401/400 = nginx reached the exporter (good).
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://homelab.nas:9001/api/export

# Frontend flags took effect if this prints a non-empty penpotFlags line.
curl -s http://homelab.nas:9001/js/config.js
```

From the ZimaOS terminal (or SSH) when something is still off:

```bash
docker logs --tail 100 big-bear-penpot-exporter
docker logs --tail 100 big-bear-penpot-backend
```

## Notes

- Secrets are never committed; only placeholders. Rotating
  `PENPOT_SECRET_KEY` (same value on backend and exporter) logs everyone out
  once, nothing else.
- `PENPOT_PUBLIC_URI` is `http://homelab.nas:9001`. If you open Penpot by IP
  instead, links in emails will still say `homelab.nas`; the app itself works.
- The MCP service publishes 19400 (plugin server), 19401 (MCP HTTP `/mcp`),
  19402 (plugin websocket) on the NAS, and the frontend also proxies
  `/mcp/stream`, `/mcp/sse`, `/mcp/ws` on 9001 via the `enable-mcp` flag.
