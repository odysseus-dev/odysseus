---
name: odysseus-doctor
description: Use when diagnosing Odysseus self-hosting, install, startup, readiness, Docker, reverse proxy, model endpoint, Cookbook, ChromaDB, SearXNG, ntfy, email, calendar, or local deployment problems.
version: 1.0.0
category: operations
tags: [odysseus, self-hosting, diagnostics, docker, cookbook, deployment]
platforms: [linux, macos, wsl]
status: published
confidence: 0.9
source: taught
created: 2026-06-03T00:00:00Z
---

## When to Use

Use this skill when the user says Odysseus is broken, unavailable, slow, degraded, failing to start, failing readiness checks, unable to connect to models, unable to serve/download models, or misbehaving behind Docker, native Python, WSL, macOS launcher, Tailscale, Cloudflare, nginx, Caddy, or another reverse proxy.

Do not use this for generic coding bugs, UI feature requests, or model-quality complaints unless the symptom points to deployment, configuration, connectivity, storage, or service health.

## Procedure

1. Open like a doctor: name the symptom, keep the tone calm, and avoid guessing. Say you will triage first, then check vitals, then prescribe the next smallest fix.
2. Collect intake before asking for logs: install method, OS, how it was started, URL being opened, whether auth is enabled, whether it is local-only, LAN, VPN, or reverse-proxied, and the exact visible symptom.
3. Give a privacy warning before any paste: ask the user to remove API keys, passwords, bearer tokens, webhook URLs, public IPs, private email contents, and personal documents from logs or screenshots.
4. If the repository checkout is available, offer the bundled vitals helper: `docs/skills/odysseus-doctor/scripts/collect-vitals.sh`. It is read-only and avoids log contents by default. Ask for logs separately only when the vitals point to a service.
5. Ask for only the vitals relevant to the install method. Use the Doctor Bag below as the command menu; do not ask for every command at once.
6. Separate liveness from readiness. `/api/health` only says the web process is alive. `/api/ready` is the patient chart: read its JSON and identify the failing subsystem before recommending restarts.
7. Diagnose by syndrome using the Symptom Index below. Startup failures usually come from missing Python version, dependencies, permissions, data directory, port conflicts, or env parsing. Browser cannot connect usually means wrong port, wrong bind address, service not running, container unhealthy, firewall, or reverse proxy target mismatch. Login loops usually mean cookie/security/proxy/header mismatch or first-run auth state. Degraded memory/search usually means ChromaDB or SearXNG is down or unreachable. Model failures usually mean wrong OpenAI-compatible base URL, host/container networking mismatch, missing API key, model list mismatch, or unsupported provider shape. Cookbook failures usually need task logs, backend, model id, host, GPU, VRAM, command, and whether the failure is download, dependency setup, warmup, or serve readiness.
8. Treat reverse proxies and tunnels as high-risk. Keep `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, and use HTTPS with `SECURE_COOKIES=true` behind a trusted proxy. Never suggest exposing raw ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, databases, or model API ports to the public internet.
9. For Docker networking, remember that services inside the Odysseus container do not reach host loopback via `localhost`. Use `host.docker.internal` for host services such as Ollama, and use compose service names such as `chromadb` and `searxng` for bundled services.
10. For GPU/Cookbook issues, distinguish device passthrough from runtime support. Seeing `nvidia-smi` or `/dev/kfd` inside a container proves device visibility, not that llama.cpp, vLLM, CUDA Toolkit, ROCm userspace, or the selected quantization format is usable.
11. Prescribe one change at a time. Give the exact command or setting, explain what result would confirm improvement, then ask for the next vital. Avoid shotgun fixes, broad reinstalls, or destructive cleanup unless the user explicitly approves.
12. End each response with a short doctor-style chart: `Working diagnosis`, `Next test`, `Prescription`, and `Red flags` when relevant.

## Pitfalls

- Do not ask for full logs before warning about secrets and private data.
- Do not treat `/api/health` as proof the deployment is usable; check `/api/ready` for real readiness.
- Do not recommend `APP_BIND=0.0.0.0`, `AUTH_ENABLED=false`, or `LOCALHOST_BYPASS=true` for exposed deployments.
- Do not tell Docker users to use `localhost:11434` for host Ollama from inside the container; use `host.docker.internal:11434`.
- Do not confuse Docker GPU passthrough with working CUDA/ROCm-enabled serving binaries.
- Do not restart or rebuild everything as the first response. Get the symptom and one relevant vital first.
- Do not give ten possible fixes at once. A doctor orders targeted tests.
- Do not paste full `.env`, `data/auth.json`, database rows, email bodies, API-token tables, or raw vault output into chat.
- Do not run destructive commands such as `rm -rf data`, `docker compose down -v`, database deletion, volume deletion, or broad permission resets unless the user explicitly asks and understands the data loss.

## Verification

- The user receives a concrete diagnosis path, not a generic checklist.
- Every requested command or log snippet has a stated purpose.
- The answer redacts or asks the user to redact secrets before sharing output.
- The suggested fix is scoped to the observed symptom and names the expected confirming result.
- The final response includes a compact doctor-style chart with `Working diagnosis`, `Next test`, and `Prescription`.

## Doctor Bag

Use these as targeted tools. Pick the smallest set that fits the symptom.

### Install Note

This folder is a contributed skill example, not an automatically loaded runtime skill. In a running Odysseus instance, import it through the Skills UI or install the folder under `data/skills/operations/odysseus-doctor/` so the app can discover its `SKILL.md` and bundled `scripts/` directory.

### One-Shot Vitals

From the repository root:

```bash
docs/skills/odysseus-doctor/scripts/collect-vitals.sh
```

Override the app URL when the port or host is different:

```bash
ODYSSEUS_URL=http://127.0.0.1:7860 docs/skills/odysseus-doctor/scripts/collect-vitals.sh
```

### Web Process

```bash
curl -i http://127.0.0.1:7000/api/health
curl -i http://127.0.0.1:7000/api/ready
curl -i http://127.0.0.1:7000/api/runtime
```

Interpretation: `/api/health` proves the HTTP process responded. `/api/ready` proves critical local-first storage is usable. `/api/runtime` helps separate Docker, host, and Ollama endpoint assumptions.

### Docker

```bash
docker compose ps
docker compose config
docker compose logs --tail=120 odysseus
docker compose logs --tail=80 chromadb
docker compose logs --tail=80 searxng
docker compose logs --tail=80 ntfy
```

Use logs only after warning about secrets. Prefer `ps` and `/api/ready` first.

### Native Python

```bash
python --version
python -m compileall app.py routes src core services
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

If the user is on macOS and used the launcher, check:

```bash
./start-macos.sh
curl -i http://127.0.0.1:7860/api/ready
```

### Cookbook And GPU

```bash
scripts/check-docker-gpu.sh
scripts/check-docker-gpu.sh --print-install-commands
scripts/check-docker-amd-gpu.sh
docker compose exec odysseus nvidia-smi -L
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'
```

Use the first two scripts for host/container passthrough diagnosis. Use Cookbook task logs for serve failures, because vLLM, llama.cpp, Ollama, SGLang, quantization, context length, and gated Hugging Face repos fail differently.

### Model Endpoint Checks

For host Ollama from Docker, the common endpoint is:

```text
http://host.docker.internal:11434/v1
```

For native Odysseus talking to native Ollama, the common endpoint is:

```text
http://127.0.0.1:11434/v1
```

Probe OpenAI-compatible model APIs with the app's model setup UI when possible. If using curl, do not print API keys into chat.

## Symptom Index

| Symptom | First Test | Likely Diagnosis | Prescription |
|---|---|---|---|
| Browser cannot open Odysseus | `docker compose ps` or start terminal output, then `/api/health` | Process down, wrong port, bind mismatch, firewall, reverse proxy target mismatch | Confirm port, start service, or change `APP_PORT`; do not expose broadly without auth |
| `/api/health` works but app features fail | `/api/ready` | DB, data dir, local storage, or dependency readiness issue | Fix the failing readiness item before restarting randomly |
| Docker search degraded | `docker compose ps searxng` and `docker compose logs --tail=80 searxng` | SearXNG unhealthy, bad settings volume, service URL mismatch | In Docker, `SEARXNG_INSTANCE` should point at `http://searxng:8080` inside the app |
| Memory/vector degraded | `docker compose ps chromadb` and `/api/ready` | ChromaDB down, wrong host/port, startup race, vector service unavailable | Check ChromaDB service and app env `CHROMADB_HOST` / `CHROMADB_PORT` |
| Host Ollama works but Docker Odysseus cannot see it | App endpoint config and `/api/runtime` | Container is trying `localhost` instead of host gateway | Use `http://host.docker.internal:11434/v1` and ensure Ollama listens beyond its own loopback when needed |
| Cookbook download fails | Cookbook task log, model repo id, HF token presence | Gated repo, network failure, cache permission, interrupted download | Verify HF access/license, token, and `data/huggingface` persistence |
| Cookbook serve crashes | Cookbook task log, backend, model id, quant, context, GPU/VRAM | OOM, incompatible quant, wrong tensor parallelism, missing runtime package | Match fix to error pattern; lower context or GPU memory only when the log indicates memory pressure |
| NVIDIA passthrough unclear | `scripts/check-docker-gpu.sh` | Docker runtime or overlay missing | Enable overlay only after passthrough succeeds |
| AMD passthrough unclear | `scripts/check-docker-amd-gpu.sh` | Missing `/dev/kfd`, `/dev/dri`, render group, or ROCm userspace | Apply reported compose overlay and `RENDER_GID`; then verify devices |
| Login loop behind proxy | Proxy headers, cookie settings, `AUTH_ENABLED`, `LOCALHOST_BYPASS`, `SECURE_COOKIES` | HTTP/HTTPS cookie mismatch or unsafe localhost bypass assumption | Keep auth on, bypass off, set secure cookies only behind HTTPS |

## Interpretation Notes

- A healthy Docker container can still serve a broken app if `/api/ready` fails.
- `docker compose config` catches bad environment interpolation before rebuilds.
- `APP_BIND=127.0.0.1` is the default safe host bind. `APP_BIND=0.0.0.0` is only for intentional LAN or reverse-proxy exposure with auth.
- `AUTH_ENABLED=false` is a local development setting, not a deployment fix.
- `LOCALHOST_BYPASS=true` is especially risky behind tunnels or reverse proxies.
- `SECURE_COOKIES=true` is useful only when the browser reaches Odysseus over HTTPS.
- `docker compose down -v` removes named volumes and can destroy ChromaDB/SearXNG/ntfy state. It is not a routine prescription.
- Cookbook "GPU visible" and "model can serve on GPU" are separate diagnoses.
