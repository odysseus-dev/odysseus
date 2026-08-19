# AGENTS.md — Odysseus

Self-hosted AI workspace (Python 3.11 / FastAPI), AGPL-3.0-or-later. Full agent guide lives in `CLAUDE.md`; this file is the distilled, verified quick-reference for OpenCode sessions.

## Run / sanity-check

```bash
# Docker is the primary supported dev path (handles paths/env the host often misses)
cp .env.example .env
docker compose up -d --build
# UI: http://localhost:7000  | first admin password: docker compose logs odysseus

# Host Python (use the repo venv, not system Python)
./venv/Scripts/python.exe        # Windows
./venv/bin/python                 # Linux/macOS
pip install -r requirements.txt
python -m compileall -q app.py core routes src services scripts tests   # CI syntax check
```

## Tests (verified)

- Config lives in `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths=["tests"]`, `asyncio_mode="auto"`). Note: `CLAUDE.md` wrongly says "no pytest config" — ignore that.
- Full run: `pytest -q` (needs `./data` dir present: `mkdir -p data` — sqlite DB at `./data/app.db`).
- Focused runs use `tests/run_focus.py` with `area_*` / `sub_*` markers (`tests/_taxonomy.py` tags every test by filename at collection):
  ```bash
  ./venv/Scripts/python.exe tests/run_focus.py --area security
  ./venv/Scripts/python.exe tests/run_focus.py --area services --sub-area cookbook
  ./venv/Scripts/python.exe tests/run_focus.py --fast            # not slow
  ```
- CI's pytest job is `continue-on-error: true` (known flaky / isolation / embedding-model issues). Don't treat a green local run as proof and don't assume CI must pass.
- JS syntax: `node --check` on `static/app.js` and `static/js/**/*.js` only — skip vendored `static/lib`.

## Layout (entrypoints)

- `app.py` — main FastAPI app / wiring (~52 KB). `launcher.py` — process/service entry.
- `core/` — auth, db, sessions, models, middleware, `log_safety.py` (secret/PII redaction), `atomic_io.py` (crash-safe writes), `platform_compat.py` (OS glue).
- `companion/` — side process (pairing). `mcp_servers/` — one file per MCP tool. `integrations/` — external adapters. `routes/` — HTTP routes.

## Hard rules

- Use `core/atomic_io.py` for all state files (notes/settings/sessions). Use `core/log_safety.py` for all logging — never raw `print`/`logger.info(f"...{secret}...")`.
- Put Windows/macOS/Linux differences in `core/platform_compat.py`, not scattered conditionals.
- Never commit `.env`. Don't expose raw model/service ports publicly (see `docs/setup.md`, `THREAT_MODEL.md`).
- AGPL-3.0: distributed modifications must be open-sourced.

## Workflow

- Default branch is `dev`; `main` is more curated. Branch from `dev`.
- PR titles: `<type>(<scope>): <subject>` (e.g. `fix(auth):`, `docs(setup):`).
- Compose variants: `docker-compose.gpu-nvidia.yml`, `docker-compose.gpu-amd.yml`.

## Karpathy Coding Guidelines (imported)

Behavioral guidelines to reduce common LLM coding mistakes — ported from the Hermes `karpathy-guidelines` skill (source: multica-ai/andrej-karpathy-skills, MIT). Biases toward caution over speed; use judgment on trivial tasks.

1. **Think Before Coding** — State assumptions explicitly; ask if uncertain. Present multiple interpretations instead of silently picking one. Say so if a simpler approach exists; stop and name what's confusing.
2. **Simplicity First** — Minimum code that solves the problem. No features beyond the ask, no speculative abstraction, no error handling for impossible cases. If 200 lines could be 50, rewrite it.
3. **Surgical Changes** — Touch only what the request demands; match existing style. Remove only the imports/vars/functions *your* change made unused — don't delete pre-existing dead code unless asked.
4. **Goal-Driven Execution** — Define verifiable success criteria and loop until proven ("make tests pass", not "make it work"). State a brief plan with a verify check per step on multi-step work.
