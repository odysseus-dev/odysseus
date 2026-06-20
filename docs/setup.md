# Odysseus Setup Guide

This fork targets **macOS 14+ on Apple Silicon (arm64)**. Run Odysseus natively for Metal-accelerated Cookbook model serving. Docker on macOS is supported as an optional CPU-only path when you do not need local GPU inference.

## Quick Start

> **Branch note:** `dev` is the default branch and contains the latest development changes, but it may be unstable. For the more stable curated branch, use [`main`](https://github.com/pewdiepie-archdaemon/odysseus/tree/main).

Defaults work out of the box: clone, run `./start-macos.sh`, then configure models/search/email inside **Settings**. Only edit `.env` for deployment-level overrides like `APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, or a pre-seeded admin password.

On first setup, Odysseus creates an admin account (`admin` unless `ODYSSEUS_ADMIN_USER` is set) and prints a temporary password in the terminal. Use that for the first login, then change it in **Settings**.

Contributing? See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and pull request guidelines.

### Recommended: native macOS (Apple Silicon)

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env       # optional
./start-macos.sh
```

The script installs Homebrew dependencies (`tmux`, `llama.cpp`, optional `apfel`), creates `venv/`, runs `setup.py`, starts ChromaDB locally, and launches uvicorn on port **7860** (AirPlay often holds 7000 on macOS).

To expose Odysseus on a trusted LAN or Tailscale VPN:

```bash
ODYSSEUS_HOST=0.0.0.0 ./start-macos.sh
# then open http://<tailscale-ip>:7860
```

The script reads `.env` at startup, so `APP_BIND=0.0.0.0` and `APP_PORT` set there apply without repeating flags each run.

Keep `AUTH_ENABLED=true` (the default) before binding outside loopback. Do not expose this port directly to the public internet.

To build a double-clickable `.app` wrapper:

```bash
./build-macos-app.sh
```

**Requirements:** macOS 14+, Apple Silicon, [Homebrew](https://brew.sh), Python 3.11+ (Homebrew's `/opt/homebrew/bin/python3.*` is used automatically). Cookbook needs `tmux` for background model downloads and serves.

**Cookbook on macOS:** Uses llama.cpp/Ollama with Metal. vLLM and SGLang are CUDA/ROCm-only and do not run on macOS. MLX-only models are not served by Odysseus.

### Manual native install

If you prefer not to use the launcher script:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
brew install python@3.11 tmux llama.cpp
/opt/homebrew/bin/python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7860
```

Use `--host 0.0.0.0` only when you intentionally want LAN/reverse-proxy access.

### Optional: Docker (CPU only on Mac)

Docker on macOS runs Linux in a VM with **no Metal GPU**. Cookbook serves local models on CPU only inside Docker. Prefer `./start-macos.sh` when you need GPU-accelerated local inference.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when containers are healthy. Docker Compose binds the web UI to `127.0.0.1` by default.

Manual uvicorn with the Docker default port (loopback):

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

To include optional extras in the image (PDF viewer, Office extraction; includes AGPL PyMuPDF), build with `docker compose build --build-arg INSTALL_OPTIONAL=true` before `up`.

<details>
<summary>Docker bundled services, storage, Ollama, and troubleshooting</summary>

**Bundled services.** Compose starts Odysseus, ChromaDB, SearXNG, and ntfy. Ports bind to `127.0.0.1` by default unless you opt into LAN access via `APP_BIND=0.0.0.0`.

**Cookbook storage in Docker.** Downloads live in `./data/huggingface` (`~/.cache/huggingface` in the container). Cookbook-installed Python CLIs and serve engines live in `./data/local` (`~/.local` in the container).

**Remote servers.** In **Cookbook → Settings → Servers**, generate the Odysseus SSH key and add the public key to the remote server's `~/.ssh/authorized_keys`:

```bash
ssh-copy-id -i data/ssh/id_ed25519.pub user@server
```

**Ollama with Docker.** If Ollama runs on the Mac host, add this endpoint in Settings:

```text
http://host.docker.internal:11434/v1
```

Ollama must listen outside loopback:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

**Useful checks.**

```bash
docker compose ps
docker compose logs --tail=120 odysseus
docker compose logs odysseus | grep -E 'ChromaDB|MemoryVectorStore|DEGRADED'
```

> NVIDIA/AMD GPU Docker overlays (`docker/gpu.*.yml`, `scripts/check-docker-gpu.sh`) target Linux hosts with GPU passthrough. They are not applicable to native macOS Metal serving and are not maintained in this Apple Silicon–focused fork.

</details>

## Troubleshooting & Advanced Setup

### Wrong Python architecture (Rosetta / x86_64)

On Apple Silicon, use Homebrew Python under `/opt/homebrew`, not `/usr/local` or a universal2 python.org installer. A mismatched venv causes extension load errors ("incompatible architecture") when Cookbook starts. Fix:

```bash
rm -rf venv
brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### `chromadb-client` conflicts with embedded ChromaDB

If `chromadb-client` (the lightweight HTTP-only package) is installed alongside the full `chromadb` package, Odysseus starts but ChromaDB silently falls back to HTTP-only mode and fails.

**Fix:**

```bash
./venv/bin/pip uninstall chromadb-client -y
./venv/bin/pip install --force-reinstall chromadb
```

`./start-macos.sh` performs this cleanup automatically when detected.

### HTTPS + LAN/Tailscale exposure

1. Change the bind address to `0.0.0.0` in `.env` (`APP_BIND=0.0.0.0` or `ODYSSEUS_HOST=0.0.0.0`).
2. Generate a locally-trusted cert for your LAN/Tailscale IPs using [mkcert](https://github.com/FiloSottile/mkcert):

   ```bash
   mkcert -install
   mkcert -cert-file cert.pem -key-file key.pem 192.168.1.100 tailscale-ip
   ```

3. Run uvicorn with the generated certs:

   ```bash
   python -m uvicorn app:app --host 0.0.0.0 --port 7860 --ssl-certfile=cert.pem --ssl-keyfile=key.pem
   ```

4. Install the `mkcert` CA on any other device you want to access Odysseus from.

### Optional Dependencies

`requirements-optional.txt` contains packages that unlock extra features. It is not installed by default.

| Package | Feature unlocked |
|---------|-----------------|
| `faster-whisper` | Local speech-to-text (microphone → text) via the "local" STT provider. |
| `ddgs` | DuckDuckGo as a search provider option. |
| `PyMuPDF` | PDF page rendering in the side viewer panel and form-filling. (Note: AGPL-3.0) |
| `markitdown` | Office/EPUB document text extraction (converts .docx/.xlsx/.pptx/.xls/.epub to Markdown). |

### Faster, reproducible installs with uv (optional)

[uv](https://docs.astral.sh/uv/) works as a drop-in replacement for the venv + pip steps:

```bash
uv venv venv --python 3.13
uv pip install -r requirements.txt
python setup.py
```

Snapshot exact versions on macOS arm64 when you want reproducible installs:

```bash
uv pip compile requirements.txt -o requirements.lock
uv pip sync requirements.lock
```

`requirements.lock` is gitignored and platform-specific — compile it on the Mac you deploy to.

### Outlook / Office 365 email

Odysseus email accounts currently use IMAP/SMTP username-password auth. Outlook and Microsoft 365 generally require OAuth instead. See [docs/email-outlook.md](docs/email-outlook.md).

## Security Notes

Odysseus is a self-hosted workspace with powerful local tools: shell access, file uploads, model downloads, web research, email/calendar integrations, and API tokens. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.
- Use `SECURE_COOKIES=true` when Odysseus is served through HTTPS by a trusted reverse proxy or private access gateway.
- Do not expose it directly to the public internet without HTTPS and a trusted reverse proxy or private access layer.
- Keep `.env`, `data/`, `logs/`, databases, uploads, generated media, backups, auth/session files, API keys, and model/provider tokens out of Git and private shares.
- Review `data/auth.json` after first boot: disable open signup unless intentional, make only your own account admin.
- Prefer binding manual development runs to `127.0.0.1`; bind to `0.0.0.0` only when you intentionally want LAN/reverse-proxy access.
- Keep ChromaDB, SearXNG, ntfy, Ollama, llama.cpp, databases, and raw model/provider APIs internal-only.

### Private or proxied deployments

Odysseus serves plain HTTP on its app port. A typical production/private setup on macOS:

1. Keep Odysseus on localhost, for example `127.0.0.1:7860`.
2. Terminate HTTPS at a trusted reverse proxy or private access gateway (Caddy, nginx, Cloudflare Access, Tailscale, etc.).
3. Put the authenticated Odysseus web/API entrypoint behind that layer.

Common internal-only ports from the default setup:

| Port | Service |
|---|---|
| `7860` | Odysseus native default (macOS) |
| `7000` | Odysseus Docker default |
| `8080` | SearXNG (Docker) |
| `8091` | ntfy (Docker) |
| `8100` | ChromaDB (native manual / Docker host mapping) |
| `11434` | Ollama |
| `11435` | Apfel (optional, native bootstrap) |

## Configuration

Most setup is done inside the app with `/setup` or **Settings**. Use `.env` for deployment-level defaults and secrets you want present before first boot.

Key settings:

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server (e.g. `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | -- | Optional OpenAI key. Prefer adding providers in the app unless pre-seeding. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL. Docker overrides this to `http://searxng:8080`. |
| `APP_BIND` | `127.0.0.1` | Host bind address. Use `0.0.0.0` only for intentional LAN/reverse-proxy access. |
| `APP_PORT` | `7000` | Port (Docker default). Native macOS launcher defaults to `7860`. |
| `APP_DATA_DIR` | `./data` | Application data directory. |
| `APP_LOGS_DIR` | `./logs` | Application logs directory. |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for loopback requests. |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | Comma-separated permitted origins for cross-origin clients. |
| `SECURE_COOKIES` | `false` | Set true when serving through HTTPS at a trusted proxy. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | `localhost` | ChromaDB host. Docker overrides to `chromadb`. |
| `CHROMADB_PORT` | `8100` | ChromaDB port for native runs. Docker overrides to `8000`. |

### Built-in MCP servers (optional setup)

Odysseus auto-registers built-in MCP servers at startup. The npx-based browser server (`@playwright/mcp`) only starts when its npm package is already cached locally.

To enable the browser MCP, run once:

```bash
npx -y @playwright/mcp@latest --version
```

Restart Odysseus afterward.

## Architecture

```
app.py                   # FastAPI entry point
core/      auth, database, middleware, constants
src/       llm_core, agent_loop, agent_tools, chat_processor, search/
routes/    chat, session, document, memory, model … endpoints
services/  docs, memory, search, hwfit (Cookbook) …
static/    index.html + app.js + style.css + js/ (modular front-end)
docs/      landing page (index.html) + preview clips
```

## Data

All user data lives in `data/` (gitignored): `app.db` (sessions, messages, documents), `memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/`, `settings.json`.

To back up or restore everything in `data/`, see the [Backup & Restore guide](docs/backup-restore.md).
