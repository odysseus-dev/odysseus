<p align="center">
  <img src="docs/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

## Modified Distribution Notice

This repository is a modified public distribution of
[pewdiepie-archdaemon/odysseus](https://github.com/pewdiepie-archdaemon/odysseus),
licensed under the GNU Affero General Public License v3.0. Local modifications
for the Simple Signal desktop extension and Windows installer were made on
June 14, 2026. The original AGPL-3.0 license is preserved in [LICENSE](LICENSE).

> **Branch note:** `dev` is the default branch and contains the latest development changes, but it may be unstable. For the more stable curated branch, use [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main).

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="docs/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <a href="https://repology.org/project/odysseus-ai/versions"><img src="https://repology.org/badge/vertical-allrepos/odysseus-ai.svg" alt="Packaging status"></a>
</p>

<p align="center">
  <img src="docs/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Quick Start

> `dev` is the default branch and gets the newest changes first. Use [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main) if you want the more curated branch.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](docs/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

## Demo

```bash
ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh
# then open http://<tailscale-ip>:7860
```

The script also reads `.env` at startup, so `APP_BIND=0.0.0.0` and `APP_PORT`
set there are picked up automatically without a command-line override each run.

Keep `AUTH_ENABLED=true` (the default) before binding outside loopback. Do not
expose this port directly to the public internet. To build a clickable app wrapper:

```bash
./build-macos-app.sh
```

<details>
<summary>Cookbook, GPU, Ollama, and troubleshooting notes</summary>

**Docker bundled services.** Compose starts Odysseus, ChromaDB, SearXNG, and
ntfy. Odysseus and the bundled service ports bind to `127.0.0.1` by default, so
they are reachable from the host but not exposed to your LAN/public internet
unless you opt in.

**Cookbook storage in Docker.** Downloads live in `./data/huggingface`
(`~/.cache/huggingface` in the container). Cookbook-installed Python CLIs and
serve engines live in `./data/local` (`~/.local` in the container), so they
survive container recreation.

**Remote servers.** In **Cookbook -> Settings -> Servers**, generate the
Odysseus SSH key and add the public key to the remote server's
`~/.ssh/authorized_keys`. From the host you can also run:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

**Docker GPU overlays.** CPU-only users can skip this section. Cookbook can
only detect GPUs that Docker exposes to the container — if the host runtime or
device passthrough is not configured, Cookbook sees the iGPU, another card, or
CPU instead of your intended GPU.

For NVIDIA, `scripts/check-docker-gpu.sh` diagnoses GPU passthrough and can
optionally install the host runtime or update `.env`.

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

**AMD / ROCm.** AMD setup is read-only diagnostic plus manual `.env` edit. Run:

```bash
scripts/check-docker-amd-gpu.sh
```

Then add the reported values to `.env`, replacing `RENDER_GID` with your host's
numeric render group id:

```bash
COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml
RENDER_GID=989
```

For NVIDIA/AMD GPU support, also read the comments in the selected overlay file: docker/gpu.nvidia.yml or docker/gpu.amd.yml.

**Stack-management UIs (Portainer, Coolify, Dockhand, etc.).** These tools
often accept only a single Compose file and do not reliably honor `COMPOSE_FILE`
or multiple `-f` overlays. CLI users should keep using the `COMPOSE_FILE`
overlay workflow above. For stack UIs, point the stack at one of the standalone
files instead, which bundle the base stack plus the GPU settings:

- `docker-compose.gpu-nvidia.yml` — still requires the NVIDIA Container Toolkit
  on the host.
- `docker-compose.gpu-amd.yml` — still requires host ROCm/kfd/DRI setup, the
  `video`/`render` group membership, and `RENDER_GID` when needed.

The base `docker-compose.yml` plus the `docker/gpu.*.yml` overlays remain the
source of truth; the standalone files mirror them for single-file deployments.

Verify after enabling either overlay:

```bash
docker compose exec odysseus nvidia-smi -L   # NVIDIA
docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'  # AMD
```

> **GPU passthrough ≠ llama.cpp CUDA.** `nvidia-smi` passing inside the
> container confirms Docker GPU access, but llama.cpp also needs `cudart` and
> the CUDA Toolkit at runtime. If Cookbook logs show `Unable to find cudart
> library`, `Could NOT find CUDAToolkit`, `CUDA Toolkit not found`, or
> tensors/layers assigned to CPU, that is a Cookbook/llama.cpp build issue —
> not a Docker passthrough failure. Reinstall the serve engine via
> **Cookbook → Dependencies** to get a CUDA-enabled build.
>
> The same split applies to AMD/ROCm: seeing `/dev/kfd` and `/dev/dri` inside
> the container confirms device passthrough, not ROCm userspace or a
> ROCm-enabled vLLM/llama.cpp build. `rocm-smi` and `rocminfo` are not expected
> inside the slim Odysseus image.

**Ollama with Docker.** If Ollama runs on the host, add this endpoint in
Settings:

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

**Useful checks.**

```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

**macOS details.** `start-macos.sh` installs Homebrew deps, creates the venv,
runs setup, and starts uvicorn on port `7860` because AirPlay often holds
`7000`. It uses llama.cpp/Ollama for Metal. vLLM/SGLang are CUDA/ROCm-only and
do not run on macOS. MLX-only models are not served by Odysseus.

</details>

### Native Windows

**One-command launcher** (installs Python 3.12 with `winget` if Python 3.11+ is
missing, creates the venv, installs deps, runs setup, starts the server; safe to
re-run):

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
```

Or do it by hand:

```powershell
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

If `python` points at an older interpreter, use `py -3.12` (or another installed
3.11+ version) for the venv step. If `winget` is unavailable on a fresh Windows
install, install Python 3.11+ manually first.

**Requirements:** Python 3.11+. The core app (chat, agent, memory, documents,
email, calendar, deep research) runs fully native. For full **Cookbook** background
model downloads and the agent shell tool, also install
[Git for Windows](https://git-scm.com/download/win) (provides `bash.exe`).
Local GPU *serving* of vLLM/SGLang needs Linux/WSL2; for a local model on Windows,
[Ollama](https://ollama.com/download) is the easiest path — point Odysseus at
`http://localhost:11434/v1` in Settings.

Open `http://localhost:7000`, log in with the generated admin password,
and configure everything else inside **Settings**.

## Troubleshooting & Advanced Setup

### `chromadb-client` conflicts with embedded ChromaDB
If `chromadb-client` (the lightweight HTTP-only package) is installed alongside the full `chromadb` package, Odysseus starts but ChromaDB silently falls back to HTTP-only mode and fails.

**Fix:** uninstall `chromadb-client` and force-reinstall the full package:
```bash
./venv/bin/pip uninstall chromadb-client -y
./venv/bin/pip install --force-reinstall chromadb
```

### HTTPS + LAN/Tailscale exposure
To expose Odysseus on a local network or Tailscale with HTTPS:
1. Change the bind address to `0.0.0.0` in `.env` (`APP_BIND=0.0.0.0` or `ODYSSEUS_HOST=0.0.0.0`).
2. Generate a locally-trusted cert for your LAN/Tailscale IPs using [mkcert](https://github.com/FiloSottile/mkcert):
   ```bash
   mkcert -install
   mkcert -cert-file cert.pem -key-file key.pem 192.168.1.100 tailscale-ip
   ```
3. Run `uvicorn` with the generated certs:
   ```bash
   python -m uvicorn app:app --host 0.0.0.0 --port 7000 --ssl-certfile=cert.pem --ssl-keyfile=key.pem
   ```
4. Install the `mkcert` CA on any other device you want to access Odysseus from (e.g., for iOS, email the `rootCA.pem` to yourself, install the profile, and trust it in Certificate Trust Settings).

### Optional Dependencies
`requirements-optional.txt` contains packages that unlock extra features. It is not installed by default.

| Package | Feature unlocked |
|---------|-----------------|
| `faster-whisper` | Local speech-to-text (microphone -> text) via the "local" STT provider. |
| `ddgs` | DuckDuckGo as a search provider option. |
| `PyMuPDF` | PDF page rendering in the side viewer panel and form-filling. (Note: AGPL-3.0) |
| `markitdown` | Office/EPUB document text extraction (converts .docx/.xlsx/.pptx/.xls/.epub to Markdown). |

### Outlook / Office 365 email
Odysseus email accounts currently use IMAP/SMTP username-password auth. Outlook
and Microsoft 365 generally require OAuth instead, so normal Microsoft mailbox
passwords will fail. See [docs/email-outlook.md](docs/email-outlook.md) for the
current limitation and the planned integration direction.

## Security Notes
Odysseus is a self-hosted workspace with powerful local tools: shell access, file uploads, model downloads, web research, email/calendar integrations, and API tokens. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Use `SECURE_COOKIES=true` when Odysseus is served through HTTPS by a trusted reverse proxy or private access gateway.
- Do not expose it directly to the public internet without HTTPS and a trusted reverse proxy or private access layer.
- Keep `.env`, `data/`, `logs/`, databases, uploads, generated media, backups, auth/session files, API keys, and model/provider tokens out of Git and private shares. They are ignored by default.
- Review `data/auth.json` after first boot: disable open signup unless you intentionally want it, make only your own account admin, and keep demo/test accounts non-admin.
- Non-admin users do not get shell/Python/file read/write by default, and admin-only routes/tools such as MCP management, API tokens, webhooks, model/cookbook serving, backup/vault, and app settings are admin-gated. Other features are controlled by per-user privileges, so review each user's privileges before exposing a deployment.
- Rotate any API keys or tokens that were ever pasted into a shared chat, demo, screenshot, or log.
- If you enable API tokens or webhooks, create separate tokens per integration and delete unused ones.
- Prefer binding manual development runs to `127.0.0.1`; bind to `0.0.0.0` only when you intentionally want LAN/reverse-proxy access.
- Keep ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp, databases, and raw model/provider APIs internal-only. Expose only the authenticated Odysseus web/API entrypoint through your trusted proxy or private access layer.
- Before publishing a fork, run `git status --short` and confirm no private files from `.env`, `data/`, `logs/`, uploads, backups, or local databases are staged.

### Private or proxied deployments
Odysseus serves plain HTTP on its app port. Docker Compose binds Odysseus and the bundled services to `127.0.0.1` by default, so a typical production/private setup is:

1. Keep Odysseus on localhost, for example `127.0.0.1:7000`.
2. Terminate HTTPS at a trusted reverse proxy or private access gateway.
3. Put the authenticated Odysseus web/API entrypoint behind that layer.
4. Keep raw service and model ports internal-only.

Cloudflare Access, Tailscale, Caddy, nginx, and Traefik can all fit this pattern; none are required by Odysseus. If your access layer reaches Odysseus on the same host, proxy to `http://127.0.0.1:7000` and keep `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, and `SECURE_COOKIES=true`.

Common internal-only ports from the default docs/compose setup:

| Port | Service |
|---|---|
| `7000` | Odysseus raw app port |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | ChromaDB host port for manual/compose access |
| `11434` | Ollama |
| `8000-8020` | Common local model/provider APIs |

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly. Deployment details are in the [setup guide](docs/setup.md#security-notes).

## Star History

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
