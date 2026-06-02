# Odysseus Architecture

Shortest useful map of the repository for contributors. When in doubt, read the source.

## Runtime Layers

| Layer | Path | Responsibility |
|-------|------|---------------|
| **Entry point** | `app.py` | FastAPI startup, middleware wiring, route registration, static assets |
| **Foundation** | `core/` | Auth, database models, session persistence, middleware, pure data containers |
| **Business logic** | `src/` | LLM core (hybrid tool calling: native when available, code block fallback), agent loop, chat processing, memory, model context, security helpers |
| **Routes** | `routes/` | HTTP endpoints grouped by domain. Most user-facing behavior is exposed here |
| **Services** | `services/` | Feature services with their own sub-packages (memory, search, research, hwfit, etc.) |
| **Frontend** | `static/` | Vanilla JS modules (one per feature), CSS, HTML templates. No framework — DOM manipulation via IIFE/module pattern. Chat uses SSE streaming (`chatStream.js`) |
| **MCP servers** | `mcp_servers/` | Built-in Model Context Protocol implementations |
| **Scripts** | `scripts/` | CLI tools (`odysseus-*`) for operators and maintenance |

## Request Flow

1. Browser or API client hits `app.py`
2. Middleware applies security, auth, and request-timeout checks
3. Route handlers in `routes/` validate the request and call into `src/` or `core/`
4. Persistent state lands in `data/` through `core/database.py`, `core/auth.py`, or service-specific JSON stores
5. Frontend reads route responses and updates the shell in `static/`

## Auth Model

Odysseus uses a built-in local account system for browser login. Passwords are hashed, sessions expire, and TOTP is optional per user.

### Owner Scope Isolation

Every user's data is isolated by owner ID. This applies to:
- Sessions and messages
- Documents and uploads
- Memories and skills
- Emails

**Rule:** Every query that touches user-owned data must filter by owner. Null owners indicate admin-level shared resources. The isolation gate lives in `core/session_manager.py` and route-level auth helpers in `src/auth_helpers.py`.

### Admin Gates

Admin-only routes use `core.middleware.require_admin`. API token sessions are attributed to the token owner (`effective_user`). Security routes fail closed on null-owner sessions.

## Persistence

- **SQLite** (`data/app.db`) — users, settings, MCP servers
- **ChromaDB** (`data/chroma/`) — vector embeddings for memory and RAG
- **JSON files** — `sessions.json`, `memory.json`, `presets.json`, `settings.json`, `integrations.json`, `cookbook_state.json`
- **File system** — `data/uploads/`, `data/personal_docs/`, `data/huggingface/`

## Known Debt

These are tracked issues, not bugs. Don't "fix" them without checking existing plans:

| Issue | Detail |
|-------|--------|
| Upward imports | `core/__init__.py` imports from `src/` (backwards layer). Callers should import directly from `src/` |
| Module duplication | `memory.py`, `search/`, and others exist in both `src/` and `services/`. Consolidation is tracked |

## Local Development

See [README.md](../README.md) for setup instructions. The repo supports Docker, macOS native, and Windows setups.

## Good First Contributions

- Docs that explain one hard subsystem cleanly
- Missing smoke tests for a route or flow
- CSS or accessibility fixes with a screenshot
- Small bug fixes in a single route or helper
- CI or tooling that makes the repo easier to verify

## Review Rule

Keep changes narrow. If a patch touches auth, deployment, or machine-control code, add tests and note the operational impact in the PR.
