# Odysseus — Podman Quadlets Installation Runbook

Replaces `docker-compose.yml` with rootless Podman Quadlets managed by systemd.

---

## Overview

The stack has four services:

| Service    | Image                        | Host port |
|------------|------------------------------|-----------|
| odysseus   | built from `./Dockerfile`    | 7500      |
| chromadb   | `chromadb/chroma:latest`     | 8100      |
| searxng    | `searxng/searxng:latest`     | 8080      |
| ntfy       | `binwiederhier/ntfy`         | 8091      |

---

## Prerequisites

- Podman ≥ 4.7 (Quadlet `.build` file support was added in 4.7)
- systemd user session enabled (`loginctl enable-linger $USER`)
- `XDG_RUNTIME_DIR` set (auto-set on most distros; verify with `echo $XDG_RUNTIME_DIR`)

```bash
# Check versions
podman --version
systemctl --version

# Enable linger so user units survive logout
loginctl enable-linger $USER
```

---

## Step 1 — Project layout

Clone (or copy) the repo somewhere permanent. The bind mounts below reference `$PROJECT_DIR`.

```bash
export PROJECT_DIR=$HOME/odysseus   # adjust to wherever you cloned it
cd $PROJECT_DIR
```

Pre-create bind-mount directories so Podman doesn't create them as root:

```bash
mkdir -p \
  "$PROJECT_DIR/data" \
  "$PROJECT_DIR/data/ssh" \
  "$PROJECT_DIR/data/huggingface" \
  "$PROJECT_DIR/logs"
```

Copy your `.env` file into place:

```bash
cp .env.example .env   # then fill in your values
```

---

## Step 2 — Quadlet directory

Rootless quadlets live in `~/.config/containers/systemd/`.

```bash
mkdir -p ~/.config/containers/systemd/odysseus
```

All files below go into that directory.

---

## Step 3 — Network unit

**`~/.config/containers/systemd/odysseus/odysseus.network`**

```ini
[Network]
# Shared bridge for all four services.
# Containers reference each other by their Container name.
```

---

## Step 4 — Named volume units

**`~/.config/containers/systemd/odysseus/chromadb-data.volume`**

```ini
[Volume]
```

**`~/.config/containers/systemd/odysseus/searxng-data.volume`**

```ini
[Volume]
```

**`~/.config/containers/systemd/odysseus/ntfy-cache.volume`**

```ini
[Volume]
```

Empty `[Volume]` sections are valid — Podman creates the volume with default settings.

---

## Step 5 — Build unit (odysseus image)

**`~/.config/containers/systemd/odysseus/odysseus-img.build`**

```ini
[Build]
# Builds localhost/odysseus-img:latest from the project Dockerfile.
# Re-run with: systemctl --user start odysseus-img-build
ImageTag=localhost/odysseus-img:latest
File=%h/odysseus/Dockerfile
SetWorkingDirectory=%h/odysseus
```

> Replace `%h/odysseus` if `$PROJECT_DIR` is elsewhere. `%h` expands to `$HOME`.

---

## Step 6 — Container units

### SearXNG

**`~/.config/containers/systemd/odysseus/searxng.container`**

```ini
[Unit]
Description=SearXNG search engine
After=network-online.target

[Container]
Image=searxng/searxng:latest
Network=odysseus.network
ContainerName=searxng

PublishPort=127.0.0.1:8080:8080

Volume=searxng-data.volume:/etc/searxng
Volume=%h/odysseus/config/searxng/settings.yml:/etc/searxng/settings.yml:z

Environment=SEARXNG_BASE_URL=http://localhost:8080/

HealthCmd=python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5).read(1)"
HealthInterval=5s
HealthTimeout=6s
HealthRetries=20
HealthStartPeriod=10s

[Service]
Restart=always

[Install]
WantedBy=default.target
```

### ChromaDB

**`~/.config/containers/systemd/odysseus/chromadb.container`**

```ini
[Unit]
Description=ChromaDB vector database
After=network-online.target

[Container]
Image=chromadb/chroma:latest
Network=odysseus.network
ContainerName=chromadb

PublishPort=8100:8000

Volume=chromadb-data.volume:/chroma/chroma

Environment=ANONYMIZED_TELEMETRY=FALSE

[Service]
Restart=always

[Install]
WantedBy=default.target
```

### Ntfy

**`~/.config/containers/systemd/odysseus/ntfy.container`**

```ini
[Unit]
Description=Ntfy notification server
After=network-online.target

[Container]
Image=binwiederhier/ntfy
Network=odysseus.network
ContainerName=ntfy

PublishPort=8091:80

Exec=serve

Volume=ntfy-cache.volume:/var/cache/ntfy

Environment=NTFY_BASE_URL=http://localhost:8091

[Service]
Restart=always

[Install]
WantedBy=default.target
```

### Odysseus (main app)

**`~/.config/containers/systemd/odysseus/odysseus.container`**

```ini
[Unit]
Description=Odysseus AI research assistant
After=searxng.service chromadb.service
Requires=searxng.service chromadb.service

[Container]
# Image built by the odysseus-img.build unit
Image=localhost/odysseus-img:latest
Network=odysseus.network
ContainerName=odysseus

PublishPort=7500:7000

# Bind mounts — use :z to relabel for SELinux hosts
Volume=%h/odysseus/data:/app/data:z
Volume=%h/odysseus/logs:/app/logs:z
Volume=%h/odysseus/data/ssh:/app/.ssh:z
Volume=%h/odysseus/data/huggingface:/app/.cache/huggingface:z

# Load secrets and configuration from .env
EnvironmentFile=%h/odysseus/.env

Environment=SEARXNG_INSTANCE=http://searxng:8080
Environment=CHROMADB_HOST=chromadb
Environment=CHROMADB_PORT=8000
Environment=PUID=1000
Environment=PGID=1000

[Service]
# Give searxng time to pass its healthcheck before considering this a failure.
# Adjust if your machine is slow to start searxng.
RestartSec=10s
Restart=on-failure

[Install]
WantedBy=default.target
```

> **Note on PUID/PGID:** The entrypoint script in the image reads `PUID`/`PGID` and drops to that user. If your host UID differs from 1000, override in `.env` or change the `Environment=` lines above. Check with `id -u` and `id -g`.

---

## Step 7 — Enable and start

```bash
# Reload systemd so it picks up the new unit files
systemctl --user daemon-reload

# Verify Quadlet generated the units correctly
systemctl --user list-unit-files | grep -E 'searxng|chromadb|ntfy|odysseus'

# Build the odysseus image first
systemctl --user start odysseus-img-build.service
# Watch the build (Ctrl-C when done)
journalctl --user -fu odysseus-img-build.service

# Start supporting services
systemctl --user start searxng.service chromadb.service ntfy.service

# Wait for searxng to become healthy before starting odysseus
# (optional — odysseus will retry on its own due to RestartSec=10s)
until podman healthcheck run searxng 2>/dev/null | grep -q healthy; do
  echo "Waiting for searxng health…"; sleep 5
done

# Start odysseus
systemctl --user start odysseus.service

# Enable auto-start on boot
systemctl --user enable searxng.service chromadb.service ntfy.service odysseus.service
```

---

## Step 8 — Verify

```bash
# Check all four units are active
systemctl --user status searxng chromadb ntfy odysseus

# Tail logs
journalctl --user -fu odysseus.service
journalctl --user -fu searxng.service

# Quick smoke test
curl -s http://localhost:7500/health   # or whatever health endpoint the app exposes
curl -s http://localhost:8100/api/v2/heartbeat   # chromadb
curl -s http://127.0.0.1:8080/healthz            # searxng
curl -s http://localhost:8091/v1/health          # ntfy
```

---

## Day-2 operations

### Stop / restart the stack

```bash
systemctl --user stop odysseus searxng chromadb ntfy
systemctl --user restart odysseus
```

### Update a service image

```bash
podman pull searxng/searxng:latest
systemctl --user restart searxng.service
```

### Rebuild odysseus after a code change

```bash
cd $PROJECT_DIR
systemctl --user start odysseus-img-build.service
# Wait for build to finish, then restart
systemctl --user restart odysseus.service
```

### View logs

```bash
# Follow a service
journalctl --user -fu odysseus.service

# Last 100 lines
journalctl --user -n 100 -u odysseus.service
```

### List volumes and inspect data

```bash
podman volume ls
podman volume inspect chromadb-data
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `systemctl --user` fails / "Failed to connect to bus" | `XDG_RUNTIME_DIR` is not set. Log in via a real TTY or SSH, not `su`. |
| Unit not found after daemon-reload | Quadlet parse error — run `systemd-analyze --user verify ~/.config/containers/systemd/odysseus/*.container` |
| `odysseus` fails to start | Check `journalctl --user -u odysseus.service`; searxng may not be healthy yet, let it restart |
| Bind-mount files owned by root after start | `PUID`/`PGID` mismatch — verify `id -u` matches the env vars and the entrypoint's `chown` ran |
| SELinux permission denied | Add `:z` (shared) or `:Z` (private) to `Volume=` lines |
| `localhost/odysseus-img:latest` not found | Build unit didn't finish — check `journalctl --user -u odysseus-img-build.service` |
| Port already in use | Another process owns the port — `ss -tlnp | grep <port>` to find it |
