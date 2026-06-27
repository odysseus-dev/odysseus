# Radicale for multi-machine Odysseus

Shared CalDAV calendar for a Tailscale tailnet. Run **once** on an always-on host; point every Odysseus workstation at the same collection URL.

## Quick start

```bash
cd docker/radicale
cp .env.example .env
# Edit RADICALE_BIND to this machine's Tailscale IP (tailscale ip -4)
cp config/users.example config/users
# Replace alice hash in config/users with htpasswd output
mkdir -p data
docker compose up -d
```

## Connect Odysseus

On **each** workstation:

1. Add to `.env` (or use `scripts/bootstrap-multi-machine.ps1`):

   ```env
   ODYSSEUS_ALLOW_PRIVATE_CALDAV=1
   RADICALE_URL=http://100.64.0.10:5232/alice/
   ```

   Use your real Tailscale IP and username.

2. Restart Odysseus.

3. In **Settings → Calendar → CalDAV**, add:

   | Field | Value |
   |-------|-------|
   | URL | `http://<tailscale-ip>:5232/<user>/` |
   | Username | same as `config/users` |
   | Password | same as htpasswd |

Tailscale addresses (100.64.0.0/10) are treated as private IPs — `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` is required.

## Phone / other clients

Any device on the same Tailscale network can add the same CalDAV account.

## See also

- [`docs/multi-machine.md`](../../docs/multi-machine.md) — full multi-machine layout
- [`docs/backup-restore.md`](../../docs/backup-restore.md) — backing up `docker/radicale/data`
