# Agent Contributor Guide for Odysseus

> A short, agent-focused companion to `CONTRIBUTING.md`. Read both before opening a PR.

This file is a snapshot of the conventions that bite new agent contributors most often. It will drift as the codebase moves — when in doubt, the source of truth is the file the convention refers to, and `CONTRIBUTING.md`.

## What Odysseus is

A self-hosted AI assistant with a web UI, agent loop, MCP integrations, and an optional Cookbook runtime. It is **trusted-admin software**: the user running it owns the box and the data. Anything an agent can do, the admin can do — there is no untrusted-tenant sandbox inside a single Odysseus install.

Before touching auth, middleware, or tool-security code, read [`THREAT_MODEL.md`](THREAT_MODEL.md) end-to-end.

## Commands the maintainer actually runs

There is no `ruff`, `black`, or `eslint` in CI. The real gates are:

```bash
# Focused pytest (use area markers, not the full suite)
python -m pytest tests/test_<area>.py -q
python tests/run_focus.py <area>             # curated subset the maintainer uses

# Syntax / compile gates the CI actually runs
python -m py_compile app.py routes/*.py src/*.py
python -m compileall -q routes src core services mcp_servers
node --check static/js/<file-you-changed>.js

# Docker sanity
docker compose config
docker compose up -d --build
```

If your change adds a new test file, mirror the structure of an existing one in `tests/test_<area>_<thing>.py` (owner-scoping helper, monkeypatch over globals, no real network).

## Where things live

| Area | Path | Notes |
|---|---|---|
| FastAPI app entry, route wiring | `app.py` | Slim orchestrator. New routes get wired here. |
| Auth / security primitives | `core/auth.py`, `core/middleware.py`, `src/tool_security.py` | Read `THREAT_MODEL.md` first. |
| Domain logic | `core/`, `services/` | No HTTP concerns. |
| HTTP routes | `routes/`, `routes/<domain>/` | New subpackages split off when a domain grows large. |
| Agent loop, tools, MCP | `src/`, `src/agent_tools/`, `mcp_servers/` | See "The agent loop" below. |
| Frontend | `static/` (HTML/JS/CSS), `templates/` | No emoji, no new font assets, no new colour tokens. |
| Persistence | `core/database.py`, migrations in `scripts/` | SQLite by default. |

`app.py` should stay slim. If your change adds 50+ lines of orchestration there, the routing probably belongs in `routes/<domain>/`.

## The agent loop

The runtime entry is `src/agent_loop.py`. Tools are registered in `src/agent_tools/` (split files by responsibility: admin, document, filesystem, session, subprocess, web, etc.) and discovered through `src/tool_index.py`. Provider / endpoint resolution lives in `src/llm_core.py`, `src/endpoint_resolver.py`, and `services/hwfit/`. **Cookbook** (`routes/cookbook_routes.py`, `services/cookbook/`) requires a tmux session; don't pretend it's optional in tests.

Memory and RAG are split across `services/memory/`, `services/search/`, and `src/rag_*.py`. They have separate vector stores and config; do not assume a write to one is visible in the other.

## Conventions that are easy to get wrong

- **Owner-scoping is mandatory.** Every authenticated read/write goes through `src/auth_helpers.py:owner_filter(...)` or a helper that calls it. A query that omits it is a multi-tenant leak. Look at `tests/test_<area>_owner_scope.py` for the pattern.
- **Never hardcode paths, ports, or loopback URLs.** `src/constants.py` is the single source of truth. The source tree is read-only in Docker, so absolute paths from your dev box will silently break.
- **`internal-tool` is security-critical.** It grants unconditional admin scope via `require_admin`. New tools that touch it need a maintainer review, not just CI green.
- **No Unicode emoji.** Use inline monochrome SVG or existing CSS variables. Frontend lint rejects emoji.
- **PRs target `dev`.** See `CONTRIBUTING.md` for the branch model.
- **Conventional Commits** for the title (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`, `build:`, `ci:`, `perf:`, `style:`).
- **PR description must include `## How to Test`** (≥30 chars, real commands, not `## Test Plan`). The bot rejects the wrong heading.
- **One issue, one PR.** Bulk agent-generated PRs that don't match a single issue get closed regardless of correctness — see `CONTRIBUTING.md`.

## When you should NOT open a PR

- The change is purely cosmetic in code you didn't otherwise need to touch.
- You can express the same change in a sentence of `CONTRIBUTING.md`.
- There's already an open PR for the issue (check `gh pr list --search "<issue-number>"` before coding).
- The fix is a config flag in `.env.example` the user can set themselves.

## Pointers

- `CONTRIBUTING.md` — branch model, before-you-start rules, the LLM-agent contribution policy.
- `THREAT_MODEL.md` — read before touching auth, middleware, or tool-security code.
- `SECURITY.md` — how to report vulnerabilities (do not file them as public issues).
- `docs/setup.md` — install paths beyond the Docker quickstart.
- `docs/architecture-runtime-inventory.md` — what runs where at runtime.

This file is named `CLAUDE.md` for Claude Code compatibility, but the content is agent-agnostic. If your tooling prefers `AGENTS.md`, a symlink or copy works the same way.