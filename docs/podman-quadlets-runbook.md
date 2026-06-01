# Odysseus Podman Quadlet Runbook for Rocky Linux

This replaces `docker-compose.yml` with rootless Podman Quadlets managed by the
user systemd instance on Rocky Linux 9 or newer.

The deployment keeps Odysseus on host port `7500`, matching the Compose file.
Inside the container, the app still listens on `7000`.

## Stack

| Unit | Image | Host bind |
| --- | --- | --- |
| `odysseus.service` | built from this repo's `Dockerfile` | `0.0.0.0:7500 -> 7000` |
| `chromadb.service` | `chromadb/chroma:latest` | `0.0.0.0:8100 -> 8000` |
| `searxng.service` | `searxng/searxng:latest` | `127.0.0.1:8080 -> 8080` |
| `ntfy.service` | `binwiederhier/ntfy:latest` | `0.0.0.0:8091 -> 80` |

ChromaDB, SearXNG, and ntfy use Podman named volumes. Odysseus uses bind
mounts under the repo for `data/`, `logs/`, SSH keys, and model cache so they
stay easy to inspect and back up.

## Assumptions

- Host OS: Rocky Linux 9 or newer.
- Deployment user: a normal non-root user, examples use `odysseus`.
- Project path: `/opt/odysseus/app`.
- Service account home: `/opt/odysseus`.
- SELinux: enforcing.
- Firewall: `firewalld`.
- Reverse proxy is optional and runs outside this runbook.

If you deploy from your own user instead, replace `/opt/odysseus/app` with the
repo path and skip the service-account creation.

## 1. Prepare the Host

Install Podman and the systemd user-session pieces:

```bash
sudo dnf install -y podman shadow-utils systemd-container firewalld git
podman --version
systemctl --version
```

Quadlet support ships with modern Podman. Confirm the build unit is present:

```bash
man podman-build.unit >/dev/null
man podman-systemd.unit >/dev/null
```

Create a dedicated deployment user:

```bash
sudo useradd --system --create-home --home-dir /opt/odysseus --shell /bin/bash odysseus
sudo loginctl enable-linger odysseus
```

Linger matters because rootless user units otherwise stop when the user logs
out.

Open only the public ports you actually want reachable:

```bash
sudo firewall-cmd --permanent --add-port=7500/tcp
sudo firewall-cmd --permanent --add-port=8091/tcp
sudo firewall-cmd --reload
```

Do not open `8080` unless you intentionally want SearXNG reachable outside the
host. The runbook binds it to `127.0.0.1`.

## 2. Put the Repo in Place

As root or your admin user:

```bash
sudo mkdir -p /opt/odysseus/app
sudo chown -R odysseus:odysseus /opt/odysseus
```

As `odysseus`, clone or copy the repo:

```bash
sudo -iu odysseus
git clone <your-odysseus-repo-url> /opt/odysseus/app
cd /opt/odysseus/app
```

Create persistent bind-mount directories:

```bash
mkdir -p data data/ssh data/huggingface logs
chmod 700 data/ssh
```

Create `.env`:

```bash
cp .env.example .env
```

Set at least these deployment values in `.env`:

```dotenv
AUTH_ENABLED=true
LOCALHOST_BYPASS=false
SEARXNG_INSTANCE=http://searxng:8080
CHROMADB_HOST=chromadb
CHROMADB_PORT=8000
```

Then append the deployment user's UID and GID:

```bash
printf 'PUID=%s\nPGID=%s\n' "$(id -u)" "$(id -g)" >> .env
```

Optional first-boot admin seed:

```dotenv
ODYSSEUS_ADMIN_PASSWORD=replace-with-a-long-temporary-password
```

Remove it after the first successful login or rotate the password in the app.

## 3. Install the Quadlets

Rootless Quadlets live under `~/.config/containers/systemd/`. Podman scans
subdirectories, so keep this stack grouped under `odysseus/`.

```bash
mkdir -p ~/.config/containers/systemd/odysseus
cd ~/.config/containers/systemd/odysseus
```

Create `odysseus.network`:

```ini
[Network]
NetworkName=odysseus
```

Create `chromadb-data.volume`:

```ini
[Volume]
VolumeName=odysseus-chromadb-data
```

Create `searxng-data.volume`:

```ini
[Volume]
VolumeName=odysseus-searxng-data
```

Create `ntfy-cache.volume`:

```ini
[Volume]
VolumeName=odysseus-ntfy-cache
```

Create `odysseus-img.build`:

```ini
[Build]
ImageTag=localhost/odysseus-img:latest
File=/opt/odysseus/app/Dockerfile
SetWorkingDirectory=/opt/odysseus/app

[Service]
TimeoutStartSec=1800
```

Create `searxng.container`:

```ini
[Unit]
Description=Odysseus SearXNG

[Container]
Image=searxng/searxng:latest
ContainerName=searxng
Network=odysseus.network
PublishPort=127.0.0.1:8080:8080
Volume=searxng-data.volume:/etc/searxng
Volume=/opt/odysseus/app/config/searxng/settings.yml:/etc/searxng/settings.yml:Z
Environment=SEARXNG_BASE_URL=http://localhost:8080/
HealthCmd=python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5).read(1)"
HealthInterval=5s
HealthTimeout=6s
HealthRetries=20
HealthStartPeriod=10s

[Service]
Restart=always
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

Create `chromadb.container`:

```ini
[Unit]
Description=Odysseus ChromaDB

[Container]
Image=chromadb/chroma:latest
ContainerName=chromadb
Network=odysseus.network
PublishPort=8100:8000
Volume=chromadb-data.volume:/chroma/chroma
Environment=ANONYMIZED_TELEMETRY=FALSE

[Service]
Restart=always
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

Create `ntfy.container`:

```ini
[Unit]
Description=Odysseus ntfy

[Container]
Image=binwiederhier/ntfy:latest
ContainerName=ntfy
Network=odysseus.network
PublishPort=8091:80
Exec=serve
Volume=ntfy-cache.volume:/var/cache/ntfy
Environment=NTFY_BASE_URL=http://localhost:8091

[Service]
Restart=always
TimeoutStartSec=300

[Install]
WantedBy=default.target
```

Create `odysseus.container`:

```ini
[Unit]
Description=Odysseus
Requires=searxng.container chromadb.container
After=searxng.container chromadb.container

[Container]
Image=localhost/odysseus-img:latest
ContainerName=odysseus
Network=odysseus.network
PublishPort=7500:7000
Volume=/opt/odysseus/app/data:/app/data:Z
Volume=/opt/odysseus/app/logs:/app/logs:Z
Volume=/opt/odysseus/app/data/ssh:/app/.ssh:Z
Volume=/opt/odysseus/app/data/huggingface:/app/.cache/huggingface:Z
EnvironmentFile=/opt/odysseus/app/.env
Environment=SEARXNG_INSTANCE=http://searxng:8080
Environment=CHROMADB_HOST=chromadb
Environment=CHROMADB_PORT=8000

[Service]
Restart=on-failure
RestartSec=10s
TimeoutStartSec=600

[Install]
WantedBy=default.target
```

The image build is intentionally a separate unit. That keeps app restarts fast
and makes rebuilds explicit.

## 4. Generate, Build, and Start

Reload the user systemd manager:

```bash
systemctl --user daemon-reload
```

Check that Quadlet generated units:

```bash
systemctl --user list-unit-files | grep -E 'odysseus|searxng|chromadb|ntfy'
```

If a unit is missing, inspect the generator output:

```bash
/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun
```

Build the Odysseus image:

```bash
systemctl --user start odysseus-img-build.service
journalctl --user -u odysseus-img-build.service -f
```

If `systemctl --user` cannot find the user bus from a `sudo -iu odysseus`
shell, set it explicitly and retry:

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
```

Start the stack:

```bash
systemctl --user start chromadb.service searxng.service ntfy.service
systemctl --user start odysseus.service
```

The `[Install]` sections make the generated services start on future boots
after `daemon-reload`. You do not need to run `systemctl --user enable` on the
generated Quadlet services.

## 5. Verify

Check units:

```bash
systemctl --user status chromadb.service searxng.service ntfy.service odysseus.service
```

Check containers:

```bash
podman ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Smoke-test the app and bundled services:

```bash
curl -fsS http://localhost:7500/api/health
curl -fsS http://localhost:8100/api/v2/heartbeat
curl -fsS http://127.0.0.1:8080/
curl -fsS http://localhost:8091/v1/health
```

Expected Odysseus response:

```json
{"status":"healthy","timestamp":"..."}
```

Watch the first boot logs:

```bash
journalctl --user -u odysseus.service -f
```

Look for ChromaDB and memory startup lines:

```bash
journalctl --user -u odysseus.service --no-pager | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

Open:

```text
http://<rocky-host>:7500
```

## 6. Day-2 Operations

Restart one service:

```bash
systemctl --user restart odysseus.service
```

Stop the stack:

```bash
systemctl --user stop odysseus.service searxng.service chromadb.service ntfy.service
```

Start the stack:

```bash
systemctl --user start chromadb.service searxng.service ntfy.service odysseus.service
```

Rebuild Odysseus after code changes:

```bash
cd /opt/odysseus/app
git pull
systemctl --user start odysseus-img-build.service
journalctl --user -u odysseus-img-build.service -f
systemctl --user restart odysseus.service
```

Update third-party images:

```bash
podman pull chromadb/chroma:latest
podman pull searxng/searxng:latest
podman pull binwiederhier/ntfy:latest
systemctl --user restart chromadb.service searxng.service ntfy.service
```

Tail logs:

```bash
journalctl --user -u odysseus.service -f
journalctl --user -u searxng.service -f
```

List volumes:

```bash
podman volume ls | grep odysseus
```

Backup the important local state:

```bash
tar -C /opt/odysseus/app -czf "$HOME/odysseus-bind-data-$(date +%F).tgz" data logs .env
podman volume export odysseus-chromadb-data > "$HOME/odysseus-chromadb-$(date +%F).tar"
podman volume export odysseus-searxng-data > "$HOME/odysseus-searxng-$(date +%F).tar"
podman volume export odysseus-ntfy-cache > "$HOME/odysseus-ntfy-cache-$(date +%F).tar"
```

Restore a named volume:

```bash
systemctl --user stop odysseus.service chromadb.service
podman volume import odysseus-chromadb-data odysseus-chromadb-YYYY-MM-DD.tar
systemctl --user start chromadb.service odysseus.service
```

## 7. SELinux Notes

This runbook uses `:Z` on bind mounts because each host path is private to one
container. If you intentionally share a bind-mounted path across multiple
containers, use `:z` instead.

If you get permission denials, check AVCs first:

```bash
sudo ausearch -m avc -ts recent
```

Then confirm labels were applied:

```bash
ls -Zd /opt/odysseus/app/data /opt/odysseus/app/logs
```

Avoid disabling SELinux for this stack. Bad labels are usually fixed by the
`:Z` suffix or by recreating the mount path as the deployment user.

## 8. Troubleshooting

| Symptom | Check |
| --- | --- |
| `systemctl --user` says it cannot connect to the bus | Log in as the deployment user with `sudo -iu odysseus` or SSH. Do not use `su` without a real user session. Confirm `echo $XDG_RUNTIME_DIR`. |
| Services stop after logout | Run `sudo loginctl enable-linger odysseus`. |
| Unit missing after `daemon-reload` | Run `/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun` and fix the Quadlet syntax error. |
| Build times out | Keep `TimeoutStartSec=1800` in `odysseus-img.build`, or pre-build manually with `podman build -t localhost/odysseus-img:latest /opt/odysseus/app`. |
| `localhost/odysseus-img:latest` not found | The build unit did not finish. Check `journalctl --user -u odysseus-img-build.service`. |
| Odysseus cannot reach ChromaDB | Confirm `CHROMADB_HOST=chromadb`, `CHROMADB_PORT=8000`, and all containers are on `odysseus.network`. |
| Odysseus cannot search | Confirm `SEARXNG_INSTANCE=http://searxng:8080`, then check `journalctl --user -u searxng.service`. |
| Files under `data/` are owned by the wrong UID | Set `PUID` and `PGID` in `.env` to `id -u` and `id -g` for the deployment user, then restart `odysseus.service`. |
| SELinux denies bind mounts | Keep `:Z` on private bind mounts and inspect `ausearch -m avc -ts recent`. |
| Port conflict | Run `ss -tlnp | grep -E ':7500|:8100|:8080|:8091'`. |
| App health check fails | Use `/api/health`, not `/health`: `curl -fsS http://localhost:7500/api/health`. |

## 9. References

- [Rocky Linux Podman docs](https://docs.rockylinux.org/gemstones/containers/podman/):
  Quadlet is the systemd generator used for rootless and rootful Podman
  services.
- [Upstream Podman `podman-systemd.unit(5)`](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html):
  rootless search paths, recursive subdirectory scanning, generated services,
  dependency translation, and debugging with `podman-system-generator --dryrun`.
- [Upstream Podman `podman-build.unit(5)`](https://docs.podman.io/en/latest/markdown/podman-build.unit.5.html):
  `.build` files, `ImageTag=`, `File=`, `SetWorkingDirectory=`, and build
  timeout behavior.
