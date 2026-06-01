# Odysseus Architecture

This guide is the shortest useful map of the repository for contributors.

## Runtime Layers

- `app.py`: FastAPI entry point, middleware wiring, auth gate, static assets, and route registration.
- `core/`: low-level application primitives: auth, database, constants, middleware, exceptions, session handling.
- `src/`: feature services and orchestration logic for models, agent loops, RAG, memory, chat, search, security helpers, and integrations.
- `routes/`: HTTP endpoints grouped by domain. Most user-facing behavior is exposed here.
- `static/`: frontend shell, route pages, bundled JS, CSS, and assets.
- `mcp_servers/`: built-in MCP server implementations.
- `scripts/`: operator utilities, migration helpers, and maintenance tooling.
- `docs/`: landing page demos and contributor-facing documentation.

## Request Flow

1. Browser or API client hits `app.py`.
2. Middleware applies security, auth, and request-timeout checks.
3. Route handlers in `routes/` validate the request and call into `src/` or `core/`.
4. Persistent state lands in `data/` through `core/database.py`, `core/auth.py`, or service-specific stores.
5. The frontend reads route responses and updates the shell in `static/`.

## Auth Model

Odysseus currently uses the built-in local account system for browser login.

- Passwords are stored as hashes.
- Sessions are stored locally and expire.
- Optional TOTP can be enabled per user.
- Admin-only routes and tools are still gated separately.

If you change auth behavior, update:

- `README.md`
- `SECURITY.md`
- `routes/auth_routes.py`
- auth regression tests under `tests/`

## Good First Contribution Areas

- Docs that explain one hard subsystem cleanly.
- Missing smoke tests for a route or flow.
- CSS or accessibility fixes with a screenshot.
- Small bug fixes in a single route or helper.
- CI or tooling that makes the repo easier to verify.

## Review Rule

Keep changes narrow. If a patch touches auth, deployment, or machine-control code, add tests and note the operational impact in the PR.
