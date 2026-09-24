# Linux VM OpenHands overlay

Guest target for the OpenHands + 9router overlay. This repository's Odysseus
dashboard is the only product board. Agent Canvas runs beside it. Overlay
9router is the inference plane. Do not send OpenHands or 9router through HHPE.
Do not point the overlay at the Mac host 9router.

## Access

- Fusion VM: `Ubuntu 64-bit` (guest hostname `hhpe-forge`)
- SSH: `orchestration-vm` as `agent`
- Docker: `agent` is not in group `docker`. Put a `sudo -n docker` wrapper first on `PATH`, then every `docker` / probe call works without mixing sudo:
  `printf '%s\n' '#!/bin/sh' 'exec sudo -n docker "$@"' > ~/.local/bin/docker && chmod +x ~/.local/bin/docker && export PATH="$HOME/.local/bin:$PATH"`
- Checkout: `/home/agent/work/odysseus` (not `external/vendors/odysseus`)

Power the VM on before claiming a deploy failure.

## Replace the vendored board

The guest may already run a base Odysseus compose (plus an HHPE relay file) on
`127.0.0.1:7000`. Stop that **project** so 7000 is free. Do not start a second
Odysseus. Leave relay volumes; do not compose `docker-compose.relay.yml`.

```bash
ssh orchestration-vm
export PATH="$HOME/.local/bin:$PATH"
docker compose ls
# If CONFIG FILES include docker-compose.relay.yml, down that project only.
docker compose \
  -f /home/oldmac-vm/GitHub/hhpe-hrg-project/external/vendors/odysseus/docker-compose.yml \
  -f /home/oldmac-vm/GitHub/hhpe-hrg-project/external/services/odysseus/hhpe-adapter/docker-compose.relay.yml \
  down
ss -lntp | grep 7000 || echo '7000 free'
```

## Sync and start this overlay

From the Mac repo (this branch):

```bash
rsync -az --delete \
  --exclude '.git' --exclude 'venv' --exclude '.venv' \
  --exclude '.pytest_cache' --exclude '__pycache__' \
  --exclude 'data/huggingface' --exclude 'data/local' \
  --exclude 'data/openhands-runtime-bin/hermes-agent' \
  --exclude '.env' \
  ./ orchestration-vm:/home/agent/work/odysseus/
ssh orchestration-vm
cd /home/agent/work/odysseus
test -f .env || cp .env.example .env
export PATH="$HOME/.local/bin:$PATH"
python3 scripts/install_openhands_runtime_bin.py
docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml build odysseus
docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml up -d --wait --pull never
docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml ls
python3 scripts/openhands_probe.py stack --json
```

## Stability probe (headless)

Overlay Docker is **not** Mac Docker. From the Mac checkout:

```bash
./scripts/run_overlay_stability_probe.sh
```

That SSHs to `orchestration-vm` and execs `scripts/overlay_stability_probe.py`
inside `odysseus`. The wrapper passes three compose files
(`docker-compose.yml`, `docker-compose.openhands.yml`,
`docker-compose.observability.yml`) and the guest project's config-file list.
Do not compose `docker-compose.relay.yml`.

Exit 0 means the JSON report has `"ok": true`: health, Native settings on an
`openai/cx/…` model (never `openai/auto`), a non-empty sidecar reported only
as a boolean, a catalog pick that skips `gpt-6-astra` and `-review`,
`cloud_rows` of 0, and the Native Hello/Hi pipe. The report includes
`trace_id` from the `overlay.stability` span (`odysseus.synthetic=true`).
The script never prints the virtual key.

## Native chat probe (headless)

Overlay Docker is **not** Mac Docker. From the Mac checkout (phone SSH to the
Mac is the same):

```bash
./scripts/run_overlay_native_chat_probe.sh
```

That SSHs to `orchestration-vm` and execs inside `odysseus`. A local
`docker compose exec` on the Mac fails with `Cannot connect to the Docker daemon`.

Exit 0 means 9router completions and OpenHands both returned assistant text.
The stability probe calls this pipe as its Hello/Hi step.

Keep `APP_BIND=127.0.0.1`, `APP_PORT=7000`, `AUTH_ENABLED=true`, Canvas on
`127.0.0.1:8000`. Agent Server, Automation, and overlay 9router stay unpublished.
Reach 9router with
`docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml exec -T 9router`.

`docker compose ls` for this project must list `docker-compose.yml`,
`docker-compose.openhands.yml`, and `docker-compose.observability.yml`.
It must not list `docker-compose.relay.yml`.

## After health

Connect providers from **Odysseus Settings → 9router connections** on this
board (`127.0.0.1:7000`). Overlay 9router stays unpublished. Odysseus derives
the CLI header from `NINE_ROUTER_DATA_DIR` (`machine-id` + `auth/cli-secret`
only; not the sqlite inference-key bridge).
Develop on `/home/agent/work/odysseus`. After the feature works, rebuild
`odysseus` and `up -d --wait --pull never` to confirm the packaged image.
Do not use a host-wide 9router.

## Canvas

Odysseus uses “Open in Agent Canvas” as a URL. Canvas is not a second Odysseus
board and is not embedded.

## Rollback

Previous guest Odysseus compose (without claiming it as this overlay) plus pins
in `deploy/openhands/versions.env`.
