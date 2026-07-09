# Deploying Odysseus on Coolify

Odysseus ships a Coolify-ready compose file, [`docker-compose.coolify.yml`](../../docker-compose.coolify.yml). It runs the pre-built image from GHCR (no 10-minute build on your VPS), uses named volumes only (Coolify's compose parser breaks `${VAR:-./data}/subdir`-style bind mounts), and publishes no host ports (Coolify's Traefik proxy routes to the container directly, and nothing collides with other stacks on the same server).

The regular `docker-compose.yml` is unchanged and remains the way to run Odysseus everywhere else.

## Requirements

- Coolify v4 on a server with ~2 GB free RAM and ~10 GB free disk (the image plus ChromaDB, SearXNG, and ntfy).
- A domain pointed at the server, or use the `sslip.io` domain Coolify generates for you.

## Steps

1. In Coolify: **+ New** → **Public Repository**.
2. Repository URL: `https://github.com/pewdiepie-archdaemon/odysseus`, branch `main`.
3. Set **Build Pack** to **Docker Compose** and **Docker Compose Location** to `/docker-compose.coolify.yml`, then continue.
4. Coolify lists the four services. On the `odysseus` service, set your domain (or keep the generated one). The compose file already tells the proxy to route it to port 7000 via the `SERVICE_FQDN_ODYSSEUS_7000` magic variable — you don't need to configure a port.
5. Click **Deploy**. The first deploy pulls the image (a few minutes); later deploys are fast.

## First login

- Username: `admin` (or whatever you set `ODYSSEUS_ADMIN_USER` to).
- Password: Coolify generates one on first deploy — find `SERVICE_PASSWORD_ADMIN` in the resource's **Environment Variables** tab. Change it in **Settings** after logging in.

## Notes

- **Updating:** click **Redeploy**. It re-pulls `ghcr.io/pewdiepie-archdaemon/odysseus:latest` (curated releases). For the rolling development build, change the image tag to `:dev` in the compose file or via Coolify's compose editor.
- **Persistence:** all state lives in named volumes (`odysseus-data`, `odysseus-logs`, SSH keys, the HuggingFace cache, and Cookbook-installed engines), so redeploys and image updates keep your data.
- **CORS:** `ALLOWED_ORIGINS` is set automatically from your service URL. If logins fail with CORS errors after changing the domain, check that variable matches `https://your-domain`.
- **Ollama on the same server:** the container reaches the host at `http://host.docker.internal:11434`. If Ollama runs as a separate Coolify service instead, use its service URL.
- **ntfy notifications:** the bundled ntfy service is internal-only by default. To use it from outside (e.g. the mobile app), attach a domain to the `ntfy` service in Coolify and set `NTFY_BASE_URL` to that URL.

## Why not the regular docker-compose.yml?

If you point Coolify at `docker-compose.yml` you will hit three problems:

1. Coolify rewrites bind-mount sources and mangles `${APP_DATA_DIR:-./data}/ssh`-style entries into empty strings, failing with `invalid spec: :/app/.ssh:z: empty section between colons`.
2. It builds the image from source on your server — a multi-stage build that takes 10+ minutes and can OOM small VPSes.
3. Ports are bound to `127.0.0.1` on the host and SearXNG claims host port 8080, which collides with Coolify's own proxy setups.

`docker-compose.coolify.yml` avoids all three.
