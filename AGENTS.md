# AI Agent Guide

This file is for AI coding agents working in this repository. Read it before
making changes. It is intentionally more direct than README.md and
CONTRIBUTING.md.

## Hard Rules

- Open or reference an issue before opening an agent-generated PR. Keep one
  bug fix or feature per PR.
- Keep changes scoped. Do not mix behavior fixes, UI restyling, broad
  refactors, generated files, or formatting-only churn in the same PR.
- Preserve user data and secrets. Never write API keys, tokens, passwords,
  email credentials, private logs, personal documents, or public IPs into
  commits, issues, tests, or screenshots.
- For database secrets, use `EncryptedText` in `core/database.py` or the
  existing secret/API-key storage helpers. Do not add plaintext secret columns.
- For multi-user data, enforce owner scoping. Routes that read by caller-supplied
  ids must verify ownership before returning data or using another row's model,
  endpoint, headers, quota, files, sessions, memories, or documents.
- For UI changes, follow the existing visual language. Reuse CSS variables and
  existing classes, use inline SVG icons when needed, avoid Unicode emoji in UI
  or code, and attach screenshots or clips in the PR.

## Repository Map

- `app.py` is the FastAPI orchestrator. It initializes managers and mounts route
  factories.
- `core/` contains auth, SQLAlchemy models, database setup, middleware, and
  shared constants.
- `routes/` contains API route factories. Most files expose `setup_*_routes(...)`
  and return an `APIRouter` that is mounted in `app.py`.
- `src/` contains managers, LLM/provider adapters, agent loop/tool execution,
  memory/RAG helpers, document processing, search, settings, and application
  services used by routes.
- `services/` contains domain services split out from `src/`, currently including
  search, memory/skills, and hardware/model-fit helpers.
- `static/index.html`, `static/style.css`, and `static/js/` make up the browser
  app. Large frontend features are split into modules under `static/js/`.
- `scripts/` contains CLI helpers used by users, Docker/native setup, and shell
  entrypoints.
- `data/` is runtime state and is gitignored. Do not rely on checked-in contents
  there.
- `tests/` contains regression tests. Prefer small tests that pin the exact bug
  or contract you changed.

## Change Rules

- When adding a new route, follow the route-factory pattern and mount it in
  `app.py`. Pass existing managers through `initialize_managers()` in
  `src/app_initializer.py` when the route needs shared state.
- When adding or changing a database column, add a matching `_migrate_*()`
  helper in `core/database.py` and wire it into the migration sequence. This
  project uses hand-written startup migrations, not Alembic.
- When adding owner-bearing data, add the owner column, indexes/filters, and
  tests that prove cross-user access is blocked.
- When touching provider or model-call code, keep provider-specific behavior in
  `src/llm_core.py`, `src/endpoint_resolver.py`, or the established helper for
  that provider. Do not duplicate ad hoc payload/header logic in routes.
- When touching agent tools, treat tool outputs, documents, memories, notes,
  fetched pages, and skills as untrusted data. Preserve existing
  `untrusted_context` wrapping and prompt-injection guards.
- When changing uploads, files, or paths, use existing path validation helpers
  and keep reads/writes inside the intended data directories.
- When changing environment variables, ports, install steps, feature lists, or
  architecture, update `README.md` and this file in the same PR if they become
  inaccurate.

## Frontend Rules

- Reuse existing components and styles. If a similar toolbar, modal, dropdown,
  card, chip, or button already exists, extend it instead of adding a parallel
  pattern.
- Use existing CSS variables such as `--red`, `--fg`, `--bg`, `--card`, and
  `--border`. Avoid hard-coded colors, font sizes, and spacing unless the local
  file already establishes that exact pattern.
- Keep dark theme compatibility. Light-mode work must go through the existing
  theme system.
- For JS modules that draw to the DOM, run `node --check static/js/<file>.js`.
  For visual changes, run the app and include screenshots or a short clip.

## Testing Expectations

- Run the smallest relevant checks and report them in the PR.
- Common checks:
  - `python -m pytest`
  - `python -m py_compile app.py routes/*.py src/*.py`
  - `python -m compileall -q app.py core routes services src mcp_servers`
  - `node --check static/js/<file-you-changed>.js`
  - `docker compose config` for Docker/compose changes
- If a dependency or environment prevents a check from running, say exactly what
  failed and which targeted checks did run.

## PR Checklist For Agents

- Link the issue with `Fixes #NNN`, `Closes #NNN`, or `Part of #NNN`.
- Summarize the root cause and the fix, not just the files touched.
- List tests/manual verification.
- Mention residual risk or skipped checks.
- Include screenshots/clips for anything visual.
- Keep this guide and README.md in sync with any convention or setup change.
