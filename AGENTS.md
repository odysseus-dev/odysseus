# AGENTS.md

## Commands

```bash
# Run all tests (pytest with asyncio_mode=auto)
python -m pytest

# Run a single test file
python -m pytest tests/test_agent_loop.py

# Syntax-check Python sources
python -m py_compile app.py routes/*.py src/*.py

# Syntax-check a JS module
node --check static/js/<file>.js

# Docker validation
docker compose config
docker compose up -d --build
```

No linter, formatter, or type checker is configured. No build step for frontend JS.

## Architecture

- **Backend:** FastAPI monolith. Entry point: `app.py`. Python 3.11+.
- **Frontend:** Vanilla ES modules in `static/js/` — no React, no bundler, no transpiler. `static/js/package.json` sets `"type": "module"`. Served directly by Starlette.
- **Layout:** `core/` (auth, DB, middleware, models) · `src/` (business logic: LLM, agent, search, memory, tools) · `routes/` (API endpoints) · `services/` (sub-packages: docs, memory, research, search, shell, stt, tts, youtube, hwfit) · `mcp_servers/` (built-in MCP servers) · `scripts/odysseus-*` (CLI tools for each subsystem).
- **Data:** All user data in `data/` (gitignored): `app.db` (SQLite), `chroma/`, `uploads/`, `memory_vectors/`, etc. Never commit anything from `data/` or `logs/`.
- **Config:** Runtime config via in-app Settings or `.env` (copy `.env.example`). Docker overrides several vars (e.g. `CHROMADB_HOST=chromadb`, `SEARXNG_INSTANCE=http://searxng:8080`).

## Testing

- ~370+ test files in `tests/`. `conftest.py` stubs heavy optional deps (sqlalchemy, fastapi, bcrypt, etc.) with `MagicMock` when not installed — tests run without a full install.
- `conftest.py` pre-imports real `sqlalchemy`/`core.database` before stubs can contaminate them. Do not reorder or remove those imports.
- Tests are unit-level and import modules directly; they do not start the server.
- `bombadil-spec.ts` is a UI integration spec using `@antithesishq/bombadil` (dev dep) — not a pytest file.

## Visual Style (strictly enforced)

PRs that touch anything rendered (HTML, CSS, SVG, JS DOM manipulation) **will be closed** if they violate these:

- **No Unicode emoji** in UI or code. Use inline SVG (monochrome style from `static/index.html`) or plain text.
- Reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`, …). No new color/font/spacing values.
- Reuse existing button/input/card classes. No parallel component patterns.
- Monospaced font (`Fira Code`) for primary UI text.
- Dark theme is default; light mode goes through the existing theme system.
- Attach screenshots (desktop + mobile if applicable) for any UI change.

## Gotchas

- `chromadb-client` (HTTP-only package) conflicts with embedded `chromadb`. If both are installed, ChromaDB silently degrades. Fix: `pip uninstall chromadb-client -y && pip install --force-reinstall chromadb`.
- `.env` is loaded with `encoding="utf-8-sig"` to tolerate Windows BOM. Do not change this.
- Windows: `HF_HUB_DISABLE_SYMLINKS=1` is set in `app.py` before imports. Do not move it below the dotenv load.
- macOS: `start-macos.sh` uses port `7860` (AirPlay holds `7000`). vLLM/SGLang don't run on macOS; use llama.cpp/Ollama.
- Docker Compose binds to `127.0.0.1` by default. Set `APP_BIND=0.0.0.0` only intentionally.
- `requirements-optional.txt` (faster-whisper, duckduckgo-search, PyMuPDF, markitdown) is not installed by default; features degrade gracefully without them.
