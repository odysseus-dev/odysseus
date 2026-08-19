# Odysseus

A self-hosted AI workspace (Python/FastAPI) for chat, agents, research, documents, email, notes, calendar, and local model workflows. AGPL-3.0-or-later.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
# UI: http://localhost:7000
# First admin password is printed by:
docker compose logs odysseus
```

Native installs, GPU notes, Windows/macOS, HTTPS, and config live in `docs/setup.md`.

## Common Commands

```bash
# Compose variants
docker compose up -d --build          # default
docker compose -f docker-compose.gpu-nvidia.yml up -d --build
docker compose -f docker-compose.gpu-amd.yml up -d --build

# Windows native launcher
pwsh ./launch-windows.ps1

# Portable Windows build
pwsh ./build-windows-portable.ps1

# Service install
./install-service.sh                  # Unix
```

## Architecture

```
app.py                  # FastAPI entry, routes
launcher.py             # process launcher / service entry
companion/              # companion side service (routes, pairing)
core/                   # business logic (auth, db, sessions, models, middleware,
                        # log_safety, atomic_io, exceptions, constants, platform_compat)
mcp_servers/            # MCP tool servers: email, image_gen, memory, rag
integrations/           # external adapters (claude, codex)
config/                 # non-secret config (e.g. searxng)
docker/                 # build helpers, GPU compose fragments
docs/                   # setup.md, index.html, screenshots
Odysseus.spec           # PyInstaller spec
```

**Service model:** `app.py` is the main FastAPI app. `companion/` runs a side process for pairing. `mcp_servers/` expose tools via MCP. `integrations/` are plug-in adapters.

## Code Style

- Python 3.x, FastAPI, SQLAlchemy-style models in `core/models.py`.
- Errors: custom exceptions live in `core/exceptions.py`; surface as HTTP responses via `core/middleware.py`.
- Auth: helpers and locks in `core/auth.py` (note recent `config lock around migration` change — see `core/auth.py` when touching migrations).
- File I/O: use `core/atomic_io.py` for crash-safe writes; do not write files directly.
- Logging: route everything through `core/log_safety.py` (it redacts secrets/PII).
- Platform glue: `core/platform_compat.py` is the place for Windows/macOS/Linux differences.

## Environment

- Copy `.env.example` to `.env` and edit. **Do not commit `.env`.**
- First-run admin password is logged once at startup; capture it from `docker compose logs odysseus`.
- Email: when configuring IMAP/SMTP, ports are validated (do not assume defaults — see recent port-validation fix in `core/auth.py` or related).
- `config/searxng/` is used for web search; review before exposing publicly.

## Testing / Sanity Checks

There is no top-level `pytest` config in the repo root. Standard sanity check while iterating:

```bash
python -m py_compile app.py core/*.py mcp_servers/*.py companion/*.py
```

For docker-iterated changes, prefer `docker compose up -d --build` over running the host Python directly (path/env differences are common sources of "works on my machine" bugs).

## Gotchas

- **AGPL-3.0-or-later.** Any modifications you distribute must be open-sourced under AGPL. Keep this in mind for any forks or downstream services.
- **Do not expose raw model/service ports publicly.** Security guidance in `docs/setup.md` and `THREAT_MODEL.md`.
- **First-run admin password is in logs** — rotate / persist it; do not leave it relying on log scraping.
- **Windows / macOS / Linux drift:** keep new platform-specific code in `core/platform_compat.py` rather than scattered conditionals.
- **Secrets in logs:** `core/log_safety.py` exists for a reason. Do not bypass it with raw `print` / `logger.info(f"...{secret}...")`.
- **Atomic writes:** use `core/atomic_io.py` for any state file (notes, settings, sessions) — direct writes can corrupt on crash/power loss.
- **Self-host traps:** common pitfalls are documented in the project troubleshooting cookbook — see recent `docs(setup): add a self-host troubleshooting cookbook` commit message for the entry point.

## Workflow

- `dev` is the default branch; `main` is more curated. Branch from `dev` for new work.
- PR titles follow `<type>(<scope>): <subject>` (e.g. `fix(auth):`, `docs(setup):`).
- Best entry points for contributions: fresh-install testing, provider setup bugs, mobile/editor polish, docs, small focused refactors (per `CONTRIBUTING.md`).
- Roadmap items live in `ROADMAP.md`; cross-check before starting a non-trivial feature.

## Key Files to Read First

- `README.md` — feature overview + quick start
- `docs/setup.md` — full install / config
- `THREAT_MODEL.md` — security assumptions
- `CONTRIBUTING.md` and `ROADMAP.md` — what to work on
- `app.py` — FastAPI entry; ~52 KB, the main wiring layer
- `core/` — start with `models.py`, `auth.py`, `database.py`
- `companion/routes.py` and `companion/pairing.py` — companion service
- `mcp_servers/*.py` — one file per tool server

## Karpathy Coding Guidelines (imported)

Behavioral guidelines to reduce common LLM coding mistakes — ported from the Hermes `karpathy-guidelines` skill (source: multica-ai/andrej-karpathy-skills, MIT). Biases toward caution over speed; use judgment on trivial tasks.

1. **Think Before Coding** — State assumptions explicitly; ask if uncertain. Present multiple interpretations instead of silently picking one. Say so if a simpler approach exists; stop and name what's confusing.
2. **Simplicity First** — Minimum code that solves the problem. No features beyond the ask, no speculative abstraction, no error handling for impossible cases. If 200 lines could be 50, rewrite it.
3. **Surgical Changes** — Touch only what the request demands; match existing style. Remove only the imports/vars/functions *your* change made unused — don't delete pre-existing dead code unless asked.
4. **Goal-Driven Execution** — Define verifiable success criteria and loop until proven ("make tests pass", not "make it work"). State a brief plan with a verify check per step on multi-step work.
