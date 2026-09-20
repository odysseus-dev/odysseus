# Linux VM OpenHands overlay

Guest target for the OpenHands + 9router overlay. This repository's Odysseus
dashboard is the only product board. Agent Canvas runs beside it. Overlay
9router is the inference plane. Do not send OpenHands or 9router through HHPE.
Do not point the overlay at the Mac host 9router.

## Access

- Fusion VM: `Ubuntu 64-bit` (guest hostname `hhpe-forge`)
- SSH: `orchestration-vm` as `agent`
- Docker: `sudo -n docker` (`agent` is not in group `docker`)
- Checkout: `/home/agent/work/odysseus` (not `external/vendors/odysseus`)

Power the VM on before claiming a deploy failure.

## Replace the vendored board

The guest may already run a base Odysseus compose (plus an HHPE relay file) on
`127.0.0.1:7000`. Stop that **project** so 7000 is free. Do not start a second
Odysseus. Leave relay volumes; do not compose `docker-compose.relay.yml`.

```bash
ssh orchestration-vm
sudo docker compose ls
# If CONFIG FILES include docker-compose.relay.yml, down that project only.
sudo docker compose \
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
  ./ orchestration-vm:/home/agent/work/odysseus/
ssh orchestration-vm
cd /home/agent/work/odysseus
test -f .env || cp .env.example .env
python3 scripts/install_openhands_runtime_bin.py
sudo -n docker compose -f docker-compose.yml -f docker-compose.openhands.yml build odysseus
sudo -n docker compose -f docker-compose.yml -f docker-compose.openhands.yml up -d --wait --pull never
sudo -n docker compose -f docker-compose.yml -f docker-compose.openhands.yml ls
python3 scripts/openhands_probe.py stack --json
```

Keep `APP_BIND=127.0.0.1`, `APP_PORT=7000`, `AUTH_ENABLED=true`, Canvas on
`127.0.0.1:8000`. Agent Server, Automation, and overlay 9router stay unpublished.
Reach 9router with `docker compose exec`.

`docker compose ls` for this project must list only `docker-compose.yml` and
`docker-compose.openhands.yml`.

## After health

Connect providers **inside overlay 9router on the guest**. Do not add providers
in tests first. Leave Mac `9router -H 127.0.0.1` (port 20128) untouched.

## Canvas

Odysseus uses “Open in Agent Canvas” as a URL. Canvas is not a second Odysseus
board and is not embedded.

## Rollback

Previous guest Odysseus compose (without claiming it as this overlay) plus pins
in `deploy/openhands/versions.env`.
