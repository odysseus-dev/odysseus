# Odysseus

```
───────────────────────────────────────────────
 ⊹ ࣪ ˖ ૮( ˶ᵔ ᵕ ᵔ˶ )っ  Odysseus vers. 1.0
───────────────────────────────────────────────
```

![Odysseus](docs/odysseus.jpg)

A self-hosted AI workspace -- meant to be the self-hosted version of the UI experience you get from ChatGPT and Claude. But with more jank and fun. Running on your own hardware, with your own data -- local-first, privacy-first, and no trojan.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/pewdiepie-archdaemon/odysseus?style=flat)](https://github.com/pewdiepie-archdaemon/odysseus/stargazers)
[![GitHub Discussions](https://img.shields.io/github/discussions/pewdiepie-archdaemon/odysseus)](https://github.com/pewdiepie-archdaemon/odysseus/discussions)
[![Live tour](https://img.shields.io/badge/demo-live%20tour-brightgreen)](https://pewdiepie-archdaemon.github.io/odysseus/)

## Contents

- [Features](#features)
- [Demo](#demo)
- [Install](#install)
- [First run](#first-run)
- [Configuration](#configuration)
- [GPU and Cookbook](#gpu-and-cookbook)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Architecture](#architecture)
- [Data](#data)
- [Contributing](#contributing)
- [License](#license)

## Features

| Feature | Stack / details |
|---|---|
| **Chat** -- chat with any local model or API; adding them is super simple. | vLLM · llama.cpp · Ollama · OpenRouter · OpenAI · GitHub Copilot |
| **Agent** -- hand it tools and let it run the whole task itself. | built on [opencode](https://github.com/anomalyco/opencode) · MCP · web · files · shell · skills · memory |
| **Cookbook** -- scans your hardware, recommends models, click to download and serve.. easy! | built on [llmfit](https://github.com/AlexsJones/llmfit) · VRAM-aware · GGUF / FP8 / AWQ · fit scoring · vLLM / llama.cpp serving |
| **Deep Research** -- multi-step runs that gather, read, and synthesize sources into a nice visual report. | adapted from [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch) |
| **Compare** -- a fun tool to compare models side by side. Test completely blind, no bias! | multi-model · blind test · synthesis |
| **Documents** -- YOU write the text, AI is there to assist, not the opposite. | multi-tab editor · markdown · HTML · CSV · syntax highlighting · AI edits · suggestions |
| **Memory / Skills** -- persistent memory and skills; your agent evolves over time as it better understands you and your tasks! | ChromaDB · fastembed (ONNX) · vector + keyword retrieval · import/export |
| **Email** -- IMAP/SMTP inbox with AI triage built in: urgency reminders, auto-tag, auto-summary, auto-reply drafts, auto-spam. | IMAP · SMTP · per-account routing · CalDAV-aware |
| **Notes & Tasks** -- quick notes with reminders, a todo list, and scheduled tasks the agent can act on. | note pings · checklist · cron-style tasks · ntfy / browser / email channels |
| **Calendar** -- local-first calendar with CalDAV sync to Radicale / Nextcloud / Apple / Fastmail. | CalDAV pull · .ics import/export · per-calendar colors · agent-aware |
| **Works on mobile** -- looks and runs great on your phone, not just desktop. | responsive · installable (PWA) · touch gestures |
| **Extras** -- more to explore, happy if you give it a go! | image editor · theme editor · file uploads (vision + PDF) · web search · presets · sessions · 2FA |

## Demo
A full, hover-to-play tour is on the [live landing page](https://pewdiepie-archdaemon.github.io/odysseus/) (source in `docs/index.html`).

<details>
<summary>Screenshots / clips</summary>

### Chat & Agents
![Chat & Agents](docs/chat.gif)
### Deep Research
![Deep Research](docs/research.gif)
### Compare
![Compare](docs/compare.gif)
### Documents
![Documents](docs/document.gif)
### Notes & Tasks
![Notes & Tasks](docs/notes.gif)

</details>

## Install

Defaults work out of the box: clone, run, then configure models / search / email
inside **Settings**. Only edit `.env` for deployment-level overrides like
`APP_BIND`, `APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL`, or a pre-seeded admin
password -- see [Configuration](#configuration) for the full list. After any
install method below, continue to [First run](#first-run).

**Requirements:** Docker, or Python 3.11+. A GPU is optional and only needed for
local model serving (see [GPU and Cookbook](#gpu-and-cookbook)).

### Docker (recommended)
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env       # optional, but recommended for explicit defaults
docker compose up -d --build
```
To include optional extras in the image (PDF viewer, Office extraction; includes
AGPL PyMuPDF), build with `docker compose build --build-arg INSTALL_OPTIONAL=true`
before `up`.

Compose binds the web UI to `127.0.0.1:7000` by default and starts the bundled
ChromaDB, SearXNG, and ntfy services on loopback too. If the port is taken, set
`APP_PORT=7001` in `.env` and recreate the container. Set `APP_BIND=0.0.0.0`
only when you intentionally want LAN/reverse-proxy access.

### Native Linux / macOS
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python setup.py
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```
Requirements: Python 3.11+. Cookbook also needs `tmux` for background model
downloads and serves. The app itself is lightweight; local model serving is the
heavy part and depends on the model, runtime, GPU, and VRAM, so small hosts can
connect to API or remote model servers instead. Use `--host 0.0.0.0` only when
you intentionally want LAN/reverse-proxy access.

> `pip install -r requirements.txt` installs `chromadb-client`, not the embedded
> engine. If you want vector memory without running a separate ChromaDB service,
> see [Troubleshooting](docs/troubleshooting.md#manual-installs-vector-memory-degraded-without-a-chromadb-service).

### Apple Silicon
Docker on macOS cannot use the Metal GPU. For GPU-accelerated Cookbook on an
M-series Mac, run Odysseus natively:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
./start-macos.sh
```

It launches at `http://127.0.0.1:7860`. To expose it to your phone over a trusted LAN/VPN such as Tailscale, bind all interfaces:

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

### Native Windows

**One-command launcher** (creates the venv, installs deps, runs setup, starts the
server; safe to re-run):

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
3.11+ version) for the venv step.

**Requirements:** Python 3.11+. The core app (chat, agent, memory, documents,
email, calendar, deep research) runs fully native. For full **Cookbook** background
model downloads and the agent shell tool, also install
[Git for Windows](https://git-scm.com/download/win) (provides `bash.exe`).
Local GPU *serving* of vLLM/SGLang needs Linux/WSL2; for a local model on Windows,
[Ollama](https://ollama.com/download) is the easiest path -- point Odysseus at
`http://localhost:11434/v1` in Settings.

## First run

On first setup, Odysseus creates an admin account (`admin` unless
`ODYSSEUS_ADMIN_USER` is set) and prints a **temporary password** in the
terminal. For Docker installs, the same line is in `docker compose logs odysseus`.

1. Open the app: `http://localhost:7000` (or `http://127.0.0.1:7860` if you
   launched with `start-macos.sh` on macOS).
2. Log in as `admin` with the temporary password from the terminal.
3. Change the password in **Settings**.
4. Add a model/provider, web search, and email under **Settings** (or run
   `/setup`).

Keep `AUTH_ENABLED=true` before binding outside loopback, and never expose the
app port directly to the public internet. See [Security](#security).

## Configuration
Most setup is done inside the app with `/setup` or **Settings**. Use `.env`
for deployment-level defaults and secrets you want present before first boot.
Key settings:

| Variable | Default | Description |
|---|---|---|
| `LLM_HOST` | `localhost` | Your LLM server (e.g. `llm-host.local:8000`) |
| `LLM_HOSTS` | -- | Comma-separated list for model discovery |
| `OPENAI_API_KEY` | -- | Optional OpenAI key. Prefer adding providers in the app unless pre-seeding. |
| `SEARXNG_INSTANCE` | `http://localhost:8080` | SearXNG URL. Docker overrides this to `http://searxng:8080`. |
| `SEARXNG_SECRET` | generated on first Docker boot | Optional SearXNG cookie/CSRF secret. Leave blank unless you need to pin it. |
| `APP_BIND` | `127.0.0.1` | Docker Compose host bind address for the web UI. Use `0.0.0.0` only for intentional LAN/reverse-proxy access. |
| `APP_PORT` | `7000` | Docker Compose host port for the web UI. |
| `AUTH_ENABLED` | `true` | Enable/disable login |
| `LOCALHOST_BYPASS` | `false` | Development-only auth bypass for loopback requests. Keep false for shared/network deployments. |
| `SECURE_COOKIES` | `false` | Set true when serving Odysseus through HTTPS at a trusted proxy or private access gateway. |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Database connection string |
| `CHROMADB_HOST` | `localhost` | ChromaDB host for vector memory. Docker overrides this to `chromadb`. |
| `CHROMADB_PORT` | `8100` | ChromaDB port for manual host runs. Docker overrides this to `8000`. |
| `EMBEDDING_URL` | -- | OpenAI-compatible embeddings endpoint |

### Built-in MCP servers (optional)

Odysseus auto-registers a few built-in MCP servers at startup. The npx-based ones (currently the browser server, `@playwright/mcp`) only start when their npm package is already in the local npx cache. If a package isn't cached, that server is skipped with a startup log message explaining what to do, so a fresh install does not block on a multi-minute npm download or hang if Playwright system deps are missing.

To enable the browser MCP (page navigation, screenshots, vision), run once:

```bash
npx -y @playwright/mcp@latest --version
```

That installs `@playwright/mcp` plus Playwright (~300MB total). Restart Odysseus and the server will register at startup.

## GPU and Cookbook

Cookbook scans your hardware, recommends models, and lets you download and serve
them locally. CPU-only users can skip GPU setup entirely -- Odysseus runs without
a GPU and can connect to API or remote model servers instead.

Docker GPU passthrough (NVIDIA / AMD), bundled-service notes, remote model
servers, Ollama, stack-management UIs (Portainer / Coolify), and macOS specifics
are covered here:

Full setup details: [docs/gpu-and-cookbook.md](docs/gpu-and-cookbook.md).

## Troubleshooting

Common setup issues and fixes:

- [`chromadb-client` conflicts with embedded ChromaDB](docs/troubleshooting.md#chromadb-client-conflicts-with-embedded-chromadb)
- [HTTPS + LAN / Tailscale exposure](docs/troubleshooting.md#https--lan--tailscale-exposure)
- [Optional dependencies](docs/troubleshooting.md#optional-dependencies)

Full guide: [docs/troubleshooting.md](docs/troubleshooting.md).

## Security
Odysseus is a self-hosted workspace with powerful local tools: shell access, file
uploads, model downloads, web research, email/calendar integrations, and API
tokens. Treat it like an admin console.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development, and set
  `SECURE_COOKIES=true` when serving over HTTPS behind a trusted proxy.
- Do not expose Odysseus directly to the public internet -- put the authenticated
  entrypoint behind a trusted reverse proxy or private access layer (Cloudflare
  Access, Tailscale, Caddy, nginx, Traefik).
- Prefer binding manual development runs to `127.0.0.1`; use `0.0.0.0` only when
  you intentionally want LAN/reverse-proxy access.
- Keep bundled services (ChromaDB, SearXNG, ntfy, Ollama, vLLM, llama.cpp) and
  databases internal-only; expose only the authenticated Odysseus entrypoint.
- Non-admin users do not get shell / Python / file read-write by default, and
  admin-only tools (MCP management, API tokens, webhooks, model/cookbook serving,
  backup/vault, app settings) are admin-gated. Other features are controlled by
  per-user privileges, so review each user's privileges before exposing a
  deployment.
- Review `data/auth.json` after first boot: disable open signup unless you want
  it, keep only your own account admin, and keep demo/test accounts non-admin.
- Rotate any API key or token ever pasted into a chat, demo, screenshot, or log.
  If you enable API tokens or webhooks, create a separate one per integration and
  delete unused ones.
- Keep `.env`, `data/`, `logs/`, databases, uploads, backups, and tokens out of
  Git (ignored by default). Before publishing a fork, run the checks in
  [SECURITY.md](SECURITY.md).

### Private or proxied deployments
Odysseus serves plain HTTP on its app port, bound to `127.0.0.1` by default. A
typical private setup:

1. Keep Odysseus on localhost, for example `127.0.0.1:7000`.
2. Terminate HTTPS at a trusted reverse proxy or private access gateway.
3. Put the authenticated Odysseus web/API entrypoint behind that layer.
4. Keep raw service and model ports internal-only.

Cloudflare Access, Tailscale, Caddy, nginx, and Traefik all fit this pattern;
none are required by Odysseus. Behind the proxy, keep `AUTH_ENABLED=true`,
`LOCALHOST_BYPASS=false`, and `SECURE_COOKIES=true`.

Common internal-only ports:

| Port | Service |
|---|---|
| `7000` | Odysseus raw app port |
| `8080` | SearXNG |
| `8091` | ntfy |
| `8100` | ChromaDB host port for manual/compose access |
| `11434` | Ollama |
| `8000-8020` | Common local model/provider APIs |

Full deployment guidance, fork-publishing checks, and how to report a
vulnerability: **[SECURITY.md](SECURITY.md)**.

## Architecture
```
odysseus/
├── app.py          # FastAPI entry point
├── core/           # auth, database, middleware, constants
├── src/            # llm_core, agent_loop, agent_tools, chat_processor, search/
├── routes/         # chat, session, document, memory, model … endpoints
├── services/       # docs, memory, search, hwfit (Cookbook) …
├── static/         # index.html + app.js + style.css + js/ (modular front-end)
└── docs/           # landing page (index.html) + preview clips + companion docs
```

## Data
All user data lives in `data/` (gitignored): `app.db` (sessions, messages, documents),
`memory.json`, `presets.json`, `uploads/`, `personal_docs/`, `chroma/`, `settings.json`.

## Contributing
Questions and setup help belong in
[Discussions](https://github.com/pewdiepie-archdaemon/odysseus/discussions) --
issues are for confirmed bugs and concrete proposals.

Help is welcome. The best entry points are fresh-install testing, provider setup
bugs, mobile/editor polish, docs, and small focused refactors. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and pull request
guidelines, and [ROADMAP.md](ROADMAP.md) for the current help-wanted list.

## License
MIT -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).

```
                                  |
                                 |||
                                |||||
                  |    |    |   |||||||
                 )_)  )_)  )_)   ~|~
                )___))___))___)\  |
               )____)____)_____)\\|
             _____|____|____|_____\\\__
             \                       /
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
               ~^~  all aboard!  ~^~
       ~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~~^~^~
```

## Star History

<a href="https://www.star-history.com/?repos=pewdiepie-archdaemon%2Fodysseus&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=pewdiepie-archdaemon/odysseus&type=date&legend=top-left" />
 </picture>
</a>
