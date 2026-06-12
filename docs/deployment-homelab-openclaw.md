# Homelab + OpenClaw Deployment Guide

This guide describes the intended production deployment topology for running
Odysseus on a Raspberry Pi alongside Converge/Redmine Dashboard, with OpenClaw
and Slack reaching Odysseus from macOS over LAN, Tailscale, or Caddy.

---

## Architecture Overview

```
┌─────────────────────────────────────────────┐
│  macOS                                      │
│                                             │
│  ┌──────────┐    ┌──────────────────────┐   │
│  │  Slack   │───▶│  OpenClaw (bot/CLI)  │   │
│  └──────────┘    └──────────┬───────────┘   │
│                             │ HTTPS / LAN   │
└─────────────────────────────┼───────────────┘
                              │
                  Tailscale / Caddy / LAN
                              │
┌─────────────────────────────▼───────────────────────────────┐
│  Raspberry Pi (Docker host)                                  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Docker network: odysseus_default                   │    │
│  │                                                     │    │
│  │  ┌──────────────┐    internal   ┌────────────────┐  │    │
│  │  │   odysseus   │─────────────▶│   converge /   │  │    │
│  │  │  :7000       │              │ redmine-dash    │  │    │
│  │  │              │              │   :3000         │  │    │
│  │  └──────┬───────┘              └────────────────┘  │    │
│  │         │ /var/run/docker.sock (or socket proxy)    │    │
│  │         ▼                                           │    │
│  │  homelab service containers (pihole, plex, …)       │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Bind mounts:  ./config  →  /app/config                     │
│                ./data    →  /app/data                        │
└──────────────────────────────────────────────────────────────┘
```

**Data flow summary:**

1. Slack dispatches a command to OpenClaw on macOS.
2. OpenClaw calls `POST /api/openclaw/…` on Odysseus over HTTPS (Tailscale/Caddy).
3. Odysseus reads homelab state from `config/homelab_services.json` and writes
   event state to `data/homelab_events.json`.
4. For Converge queries, Odysseus calls `http://converge:3000` over the
   internal Docker network — never exposing the Converge API key to OpenClaw.

---

## Raspberry Pi: Odysseus + Converge docker-compose

Below is a minimal overlay to add alongside the standard `docker-compose.yml`.
Save it as `docker-compose.pi.yml` and include it with:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.pi.yml docker compose up -d
```

```yaml
# docker-compose.pi.yml — Pi-specific overlay
services:
  odysseus:
    volumes:
      # Homelab service registry (read by Odysseus)
      - ./config:/app/config:z
      # Event state, DB, logs (persisted across restarts)
      - ./data:/app/data:z
      # Option A — direct Docker socket (see security note below)
      - /var/run/docker.sock:/var/run/docker.sock:ro
      # Option B — docker-socket-proxy (recommended; see below)
      # (comment out Option A and uncomment Option B + the proxy service)
    environment:
      # Converge / Redmine Dashboard — internal Docker network URL
      - CONVERGE_BASE_URL=http://converge:3000
      - CONVERGE_API_KEY=${CONVERGE_API_KEY}
      # Homelab health concurrency (default 5; tune for Pi CPU budget)
      - HOMELAB_HEALTH_CONCURRENCY=3
      # OpenClaw workflow allowlist (comma-separated names or IDs)
      - OPENCLAW_ALLOWED_WORKFLOWS=${OPENCLAW_ALLOWED_WORKFLOWS:-}
      # Allow requests from Caddy / Tailscale
      - ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-https://odysseus.yourdomain.ts.net}
      - SECURE_COOKIES=true

  converge:
    image: ghcr.io/your-org/redmine-dashboard:latest   # replace with your image
    restart: unless-stopped
    environment:
      - EXTERNAL_API_KEYS=${CONVERGE_API_KEY}           # read-only key Odysseus uses
    # converge is NOT exposed on a host port — internal only.
    # Odysseus reaches it at http://converge:3000.

  # Option B: docker-socket-proxy (safer alternative to raw socket mount)
  # Uncomment this service and swap Option A/B in the odysseus volumes above.
  #
  # socket-proxy:
  #   image: ghcr.io/tecnativa/docker-socket-proxy:latest
  #   restart: unless-stopped
  #   volumes:
  #     - /var/run/docker.sock:/var/run/docker.sock:ro
  #   environment:
  #     - CONTAINERS=1   # allow inspect; deny exec, build, push, etc.
  #     - POST=0         # deny all mutating calls
  #   ports: []          # do not expose externally
  #
  # Then set DOCKER_HOST=tcp://socket-proxy:2375 in the odysseus environment block.
```

---

## Required Environment Variables

Set these in your `.env` file on the Pi (see `.env.example` for the full list).

### Converge / Redmine Dashboard

| Variable | Example | Notes |
|---|---|---|
| `CONVERGE_BASE_URL` | `http://converge:3000` | Internal Docker network URL. Use the service name, not an IP. |
| `CONVERGE_API_KEY` | `ocl_abc123…` | Read-only key created in Converge's `EXTERNAL_API_KEYS` setting. Do not use an admin key. |

### Homelab health

| Variable | Default | Notes |
|---|---|---|
| `HOMELAB_HEALTH_CONCURRENCY` | `5` | Max parallel health checks. Set to `3` on a Pi 4 to avoid overwhelming the scheduler. Clamped 1–50. |

### OpenClaw bridge

| Variable | Example | Notes |
|---|---|---|
| `OPENCLAW_ALLOWED_WORKFLOWS` | `daily-summary,redmine-triage` | Comma-separated workflow names/IDs OpenClaw may trigger. Empty = no allowlist (scope checks only). `*` = all. |

### Auth / network

| Variable | Example | Notes |
|---|---|---|
| `ALLOWED_ORIGINS` | `https://odysseus.ts.net` | Add your Tailscale or Caddy domain. Comma-separated. |
| `SECURE_COOKIES` | `true` | Set `true` when serving over HTTPS. |
| `ODYSSEUS_ADMIN_PASSWORD` | *(secret)* | Set a strong password; the default empty value blocks startup. |

---

## Required Bind Mounts

| Host path | Container path | Purpose |
|---|---|---|
| `./config` | `/app/config` | Homelab service registry (`homelab_services.json`) |
| `./data` | `/app/data` | SQLite DB, event store (`homelab_events.json`), SSH keys, model cache |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker container health checks (Option A) |

### Homelab service registry

Copy and edit the example registry before first boot:

```bash
cp config/homelab_services.example.json config/homelab_services.json
# Edit config/homelab_services.json to list your Pi services.
```

---

## Docker socket access options

### Option A — direct socket mount

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock:ro
```

> **Security note:** Mounting the Docker socket — even with `:ro` — still grants
> significant daemon access. The `:ro` flag only prevents the bind mount itself
> from being remounted; the underlying Unix socket remains fully writable.
> Odysseus currently only issues a hard-coded `docker inspect` command with
> `shell=False`, so it does not execute arbitrary commands or mutate containers.
> For a homelab setup where you trust the container, this is acceptable. For a
> more hardened deployment, use Option B.

### Option B — docker-socket-proxy (recommended)

Run [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
as a sidecar. Configure it to expose only `CONTAINERS=1` (inspect) and block all
mutating calls (`POST=0`). Then point Odysseus at the proxy:

```yaml
# In odysseus environment:
- DOCKER_HOST=tcp://socket-proxy:2375

# socket-proxy service:
socket-proxy:
  image: ghcr.io/tecnativa/docker-socket-proxy:latest
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
  environment:
    - CONTAINERS=1
    - POST=0
```

This restricts the API surface to container read operations only, regardless of
what code runs inside the Odysseus container.

---

## Caddy reverse proxy

Add a block like this to your `Caddyfile` on the Pi (or on a separate reverse
proxy host). Replace `odysseus.yourdomain.ts.net` with your Tailscale or local
domain.

```caddy
odysseus.yourdomain.ts.net {
    # Tailscale handles mTLS; Caddy just terminates HTTPS toward the container.
    reverse_proxy localhost:7000 {
        # Forward the real client IP for auth logging.
        header_up X-Forwarded-For {remote_host}
        header_up X-Real-IP {remote_host}
    }

    # Optional: restrict to Tailscale CGNAT range only.
    # @tailscale remote_ip 100.64.0.0/10
    # handle @tailscale { reverse_proxy localhost:7000 }
    # respond 403
}
```

If you expose Odysseus publicly (not recommended), add rate-limiting and
require the `Authorization: Bearer` header at the proxy level.

---

## macOS: OpenClaw configuration

OpenClaw reads its Odysseus connection from environment variables or a config
file. Set these on your macOS machine:

```bash
# ~/.config/openclaw/.env  (or your shell profile)

# Base URL of your Odysseus instance on the Pi.
# Use the Tailscale hostname, a Caddy domain, or the LAN IP.
ODYSSEUS_BASE_URL=https://odysseus.yourdomain.ts.net

# API token from Odysseus Settings → API Tokens.
# Create it with the `openclaw_bridge` profile, which grants:
#   chat, converge:read, homelab:read,
#   events:read, events:write, events:ack, events:resolve
ODYSSEUS_API_TOKEN=ody_your_token_here
```

#### Creating the OpenClaw API token in Odysseus

1. Open Odysseus UI in your browser.
2. Go to **Settings → API Tokens → Create Token**.
3. Choose profile: **`openclaw_bridge`**.
4. Copy the generated token to `ODYSSEUS_API_TOKEN` on macOS.

The `openclaw_bridge` profile grants the minimum required scopes. Do not grant
`workflows:trigger` unless you intentionally need it.

---

## Smoke-test curl commands

Run these from macOS after deployment. Replace the token and URL.

```bash
TOKEN="ody_your_token_here"
BASE="https://odysseus.yourdomain.ts.net"

# 1. OpenClaw bridge health
#    Requires: chat scope
curl -sf -H "Authorization: Bearer $TOKEN" "$BASE/api/openclaw/health" | jq .

# 2. Converge integration health
#    Requires: converge:read scope; CONVERGE_BASE_URL + CONVERGE_API_KEY must be set
curl -sf -H "Authorization: Bearer $TOKEN" "$BASE/api/openclaw/converge/health" | jq .

# 3. Homelab service health (read-only, no event recording)
#    Requires: homelab:read scope
curl -sf -H "Authorization: Bearer $TOKEN" "$BASE/api/homelab/health" | jq .

# 4. Homelab health with event recording
#    Requires: homelab:read + events:write scopes
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/homelab/health?record_events=true" | jq .

# 5. Open event summary (top 10 open events, compact format)
#    Requires: events:read scope
curl -sf -H "Authorization: Bearer $TOKEN" "$BASE/api/events/summary" | jq .

# 6. Full event list filtered to open, limited to 10
#    Requires: events:read scope
curl -sf -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/events?status=open&limit=10" | jq .
```

Expected response shapes (fields vary; do not assert exact values):

| Route | Shape |
|---|---|
| `GET /api/openclaw/health` | `{"status":"ok","message":"OpenClaw bridge reachable","owner":"…","odysseus":{"ok":true},"task_runner":{…}}` |
| `GET /api/openclaw/converge/health` | `{"status":"ok","converge":{"configured":true,"ok":true,"health_status":200,…}}` |
| `GET /api/homelab/health` | `{"status":"ok","services":[{"name":"…","status":"ok",…}]}` |
| `GET /api/homelab/health?record_events=true` | Same shape; events written to `data/homelab_events.json` for unhealthy services |
| `GET /api/events/summary` | `{"status":"ok","events":[…]}` (compact, max 10 open events) |
| `GET /api/events?status=open&limit=10` | `{"status":"ok","events":[…]}` (full event objects) |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `503 Converge bridge not configured` | `CONVERGE_BASE_URL` or `CONVERGE_API_KEY` not set | Add to `.env` and restart |
| `403 API token missing required scope: homelab:read` | Token lacks scope | Recreate token with `openclaw_bridge` profile |
| `docker inspect` returns `check_failed` | Docker socket not mounted | Add `/var/run/docker.sock` volume or configure socket proxy |
| Health check slow with many services | Default concurrency too low/high for Pi | Tune `HOMELAB_HEALTH_CONCURRENCY` |
| CORS errors from OpenClaw | `ALLOWED_ORIGINS` missing Tailscale domain | Add domain to `ALLOWED_ORIGINS` and restart |
| Cookie warnings in browser | `SECURE_COOKIES=false` over HTTPS | Set `SECURE_COOKIES=true` |
