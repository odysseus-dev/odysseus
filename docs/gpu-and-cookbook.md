<sub>[← Back to the README](../README.md) · [Troubleshooting](troubleshooting.md)</sub>

# GPU and Cookbook

Cookbook scans your hardware, recommends models, and lets you download and serve
them locally. This guide covers the Docker bundled services, GPU passthrough
(NVIDIA / AMD), remote model servers, Ollama, stack-management UIs, and macOS
specifics.

CPU-only users can skip the GPU sections -- Odysseus runs fine without a GPU and
can connect to API or remote model servers instead.

## Docker bundled services
Compose starts Odysseus, ChromaDB, SearXNG, and ntfy. Odysseus and the bundled
service ports bind to `127.0.0.1` by default, so they are reachable from the host
but not exposed to your LAN/public internet unless you opt in.

## Cookbook storage in Docker
Downloads live in `./data/huggingface` (`~/.cache/huggingface` in the container).
Cookbook-installed Python CLIs and serve engines live in `./data/local`
(`~/.local` in the container), so they survive container recreation.

## Remote servers
In **Cookbook → Settings → Servers**, generate the Odysseus SSH key and add the
public key to the remote server's `~/.ssh/authorized_keys`. From the host you can
also run:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

## Docker GPU overlays
CPU-only users can skip this section. Cookbook can only detect GPUs that Docker
exposes to the container — if the host runtime or device passthrough is not
configured, Cookbook sees the iGPU, another card, or CPU instead of your intended
GPU.

### NVIDIA
`scripts/check-docker-gpu.sh` diagnoses GPU passthrough and can optionally install
the host runtime or update `.env`.

```bash
# Read-only diagnostic (default — installs nothing, never edits .env):
scripts/check-docker-gpu.sh

# Print OS-specific install commands without running them:
scripts/check-docker-gpu.sh --print-install-commands

# Install NVIDIA Container Toolkit on Ubuntu/Debian (requires sudo):
scripts/check-docker-gpu.sh --install-nvidia-toolkit

# Write COMPOSE_FILE to .env (only when GPU passthrough is confirmed working):
scripts/check-docker-gpu.sh --enable-nvidia-overlay

# Full assisted setup — install toolkit, then enable overlay if passthrough works:
scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
```

Safety notes:
- The app never installs host GPU runtime automatically.
- The app never edits `.env` automatically.
- `.env` is only modified when `--enable-nvidia-overlay` is explicitly passed,
  and only after GPU passthrough succeeds. `--yes` skips prompts but does not
  bypass the passthrough gate.
- `.env.bak.*` backups created by `--enable-nvidia-overlay` are ignored by
  Git and the Docker build context.

To enable manually without the script, add this to `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
```

### AMD / ROCm
AMD setup is read-only diagnostic plus manual `.env` edit. Run:

```bash
scripts/check-docker-amd-gpu.sh
```

Then add the reported values to `.env`, replacing `RENDER_GID` with your host's
numeric render group id:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

For NVIDIA/AMD GPU support, also read the comments in the selected overlay file:
`docker/gpu.nvidia.yml` or `docker/gpu.amd.yml`.

### Stack-management UIs (Portainer, Coolify, Dockhand, etc.)
These tools often accept only a single Compose file and do not reliably honor
`COMPOSE_FILE` or multiple `-f` overlays. CLI users should keep using the
`COMPOSE_FILE` overlay workflow above. For stack UIs, point the stack at one of
the standalone files instead, which bundle the base stack plus the GPU settings:

- `docker-compose.gpu-nvidia.yml` — still requires the NVIDIA Container Toolkit
  on the host.
- `docker-compose.gpu-amd.yml` — still requires host ROCm/kfd/DRI setup, the
  `video`/`render` group membership, and `RENDER_GID` when needed.

The base `docker-compose.yml` plus the `docker/gpu.*.yml` overlays remain the
source of truth; the standalone files mirror them for single-file deployments.

### Verify GPU passthrough
```bash
docker compose exec odysseus nvidia-smi -L   # NVIDIA
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # AMD
```

> **GPU passthrough ≠ llama.cpp CUDA.** `nvidia-smi` passing inside the
> container confirms Docker GPU access, but llama.cpp also needs `cudart` and
> the CUDA Toolkit at runtime. If Cookbook logs show `Unable to find cudart
> library`, `Could NOT find CUDAToolkit`, `CUDA Toolkit not found`, or
> tensors/layers assigned to CPU, that is a Cookbook/llama.cpp build issue —
> not a Docker passthrough failure. Re-install the serve engine via
> **Cookbook → Dependencies** to get a CUDA-enabled build.
>
> The same split applies to AMD/ROCm: seeing `/dev/kfd` and `/dev/dri` inside
> the container confirms device passthrough, not ROCm userspace or a
> ROCm-enabled vLLM/llama.cpp build. `rocm-smi` and `rocminfo` are not expected
> inside the slim Odysseus image.

## Ollama with Docker
If Ollama runs on the host, add this endpoint in Settings:

```text
http://host.docker.internal:11434/v1
```

Ollama must listen outside its own loopback interface:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

This connects Odysseus in Docker to an Ollama server that is already running on
your host machine; it does not start Ollama inside the container.
`host.docker.internal` is Docker's hostname for the host machine from inside the
container. Cookbook **Serve** is a separate workflow for serving downloaded
models through Odysseus/llama.cpp, so Windows users with an existing Ollama
install usually only need to add the endpoint in Settings.

## Useful checks
```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

## macOS details
`start-macos.sh` installs Homebrew deps, creates the venv, runs setup, and starts
uvicorn on port `7860` because AirPlay often holds `7000`. It uses llama.cpp/Ollama
for Metal. vLLM/SGLang are CUDA/ROCm-only and do not run on macOS. MLX-only models
are not served by Odysseus.

---
<sub>[← Back to the README](../README.md) · [Troubleshooting](troubleshooting.md)</sub>
