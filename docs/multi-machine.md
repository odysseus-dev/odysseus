# Multi-machine Odysseus (Tailscale + Radicale)

Run **one Odysseus instance per workstation**. Share calendar via **Radicale** on a Tailscale-reachable host. Discover models on other machines with **Tailscale** + `LLM_HOSTS`.

This is not a clustered deployment — each machine keeps its own `data/` (sessions, memory, uploads). Use [`backup-restore.md`](backup-restore.md) to copy state between machines when needed.

## Architecture

```text
Tailscale tailnet
├── NAS / home server
│   └── docker/radicale  (shared CalDAV, port 5232 on Tailscale IP)
├── Desktop A
│   ├── Odysseus (Docker or native)
│   └── LM Studio / Ollama (local GPU)
└── Laptop B
    ├── Odysseus
    └── Ollama
```

Each workstation:

1. Clones the same git repo.
2. Runs `scripts/bootstrap-multi-machine.ps1` (or copies `.env.example` → `.env` manually).
3. Starts Odysseus (Docker **or** native Windows — pick one per machine).
4. Points Calendar at the shared Radicale URL.

## 1. Shared Radicale (once per tailnet)

On your always-on host (NAS, mini PC):

```bash
cd docker/radicale
cp .env.example .env
# Set RADICALE_BIND=<tailscale-ip>  (tailscale ip -4)
cp config/users.example config/users
# Add bcrypt user hash to config/users
mkdir -p data
docker compose up -d
```

Full details: [`docker/radicale/README.md`](../docker/radicale/README.md).

## 2. Each workstation

### Bootstrap `.env`

**Windows:**

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-multi-machine.ps1 `
  -RadicaleUrl "http://100.64.0.10:5232/alice/" `
  -LlmHosts @("desktop-gpu", "laptop")
```

**Linux / macOS:** copy the multi-machine block from `.env.example` into `.env` and edit values.

### Validate

```bash
python scripts/multi_machine_env.py
```

Required checks pass when `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` and your Radicale URL uses a Tailscale/private IP.

### Start Odysseus

**Docker (recommended on Windows):**

```bash
docker compose up -d --build
```

Set in `.env`:

```env
LM_STUDIO_URL=http://host.docker.internal:1234
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
ODYSSEUS_ALLOW_PRIVATE_CALDAV=1
LLM_HOSTS=other-machine-tailscale-name
```

**Native Windows:**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Use `http://127.0.0.1:1234` for LM Studio instead of `host.docker.internal`.

### Calendar (each machine)

1. Ensure `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` in `.env` and restart.
2. **Settings → Calendar → CalDAV**
3. URL: `http://<radicale-tailscale-ip>:5232/<user>/`
4. Same username/password as `docker/radicale/config/users`

## 3. Tailscale features already in Odysseus

| Feature | Mechanism |
|---------|-----------|
| Remote LLM discovery | `LLM_HOSTS` + automatic Tailscale peer scan (`src/model_discovery.py`) |
| Remote UI (optional) | `APP_BIND=<tailscale-ip>` + reverse proxy / HTTPS (see [`setup.md`](setup.md)) |
| Phone notifications | `NTFY_BIND=<tailscale-ip>`, `NTFY_BASE_URL=http://<ip>:8091` |
| Private CalDAV | `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1` |

## 4. Moving to another machine

```bash
# On source machine
./scripts/odysseus-backup snapshot --out ./backups/move.tar.gz

# On target machine (after git clone + .env bootstrap)
./scripts/odysseus-backup restore ./backups/move.tar.gz --yes
```

Re-add CalDAV in Settings if the URL changed. ChromaDB vectors in Docker need a separate volume backup — see [`backup-restore.md`](backup-restore.md).

## 5. Daily commands (Windows native + sidecars)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
python scripts/verify_local_setup.py
python scripts/multi_machine_env.py
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| CalDAV "Private CalDAV IPs require…" | Set `ODYSSEUS_ALLOW_PRIVATE_CALDAV=1`, restart |
| Docker cannot reach LM Studio | `LM_STUDIO_URL=http://host.docker.internal:1234`, LM Studio listening on `0.0.0.0` |
| No models on other machine | Add its Tailscale name to `LLM_HOSTS`; ensure inference port is open on tailnet |
| Radicale unreachable | `RADICALE_BIND` must be Tailscale IP, not `127.0.0.1`, on the NAS host |
| ntfy not on phone | `NTFY_BIND` + `NTFY_BASE_URL` must use the same Tailscale IP |

## Related docs

- [`setup.md`](setup.md) — install, HTTPS, Tailscale exposure
- [`backup-restore.md`](backup-restore.md) — moving `data/` between machines
- [`docs/superpowers/plans/2026-06-25-multi-machine-tailscale-radicale.md`](superpowers/plans/2026-06-25-multi-machine-tailscale-radicale.md) — implementation plan
