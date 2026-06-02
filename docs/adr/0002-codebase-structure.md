# ADR-0002: Codebase Structure and Layer Conventions

**Status:** Accepted
**Date:** 2026-06-01
**Supersedes:** None (complements ADR-0001)

## Context

Odysseus has grown organically from a single-file prototype into 400+ files. This ADR records import conventions and known duplication so future changes have a shared reference. For runtime layers and request flow, see `docs/architecture.md`. CONTEXT.md is a living glossary — read the source for current behavior.

## Current Conventions

### Directory Layers (as they exist)

| Layer | Path | Responsibility |
|-------|------|---------------|
| **Foundation** | `core/` | Auth, database models, session persistence, middleware, pure data containers |
| **Business Logic** | `src/` | LLM core, agent loop, chat processing, memory, tools, model context |
| **Routes** | `routes/` | FastAPI route modules — one file per feature area |
| **Services** | `services/` | Feature services with their own sub-packages (memory, search, research, hwfit, etc.) |
| **Scripts** | `scripts/` | CLI entry points (`odysseus-*`) |

See `docs/architecture.md` for runtime layers and request flow.

### Import Direction (target state)

Layers import downward — a layer should not import from layers above it:
- `core/` → standard library, third-party packages only (no `src/`, `routes/`, `services/`)
- `src/` → `core/`, standard library, third-party packages (no `routes/`, `services/`)
- `routes/` → `core/`, `src/`
- `services/` → `core/`, `src/`, same service package

Cross-service imports between `services/*` sub-packages should be avoided. Shared logic belongs in `src/` or `core/`.

### Naming Conventions (as they exist)

- **Routes:** One file per domain — `{domain}_routes.py`. Helpers as `{domain}_helpers.py` in the same directory.
- **Tests:** `tests/test_{module_name}.py` — mirrors the source module being tested.
- **Frontend modules:** `static/js/{feature}.js` — one file per feature area, vanilla JS IIFE/module pattern.

### Owner Scope Isolation

All user data is isolated by owner ID. Every query that touches user-owned data (sessions, messages, documents, memories, emails) must filter by owner. Null owners indicate admin-level shared resources. Enforced in `core/session_manager.py` and checked at route level via `src/auth_helpers`.

## Known Violations (tracked, not blocking)

| Issue | Detail |
|-------|--------|
| Upward import | `core/__init__.py` imports from `src.llm_core` — callers should import directly from `src/` |
| Upward import | `src/app_initializer.py` imports from `services.memory.skills` — consider `src/` facade |
| Duplication | `memory.py`, `memory_vector.py`, `research_handler.py`, `youtube_handler.py` exist in both `src/` and `services/` |
| Duplication | `search/` directory duplicated between `src/search/` and `services/search/` with diverging implementations |

## Future Work (not in scope)

- **Type annotations** — most functions lack them. Would enable mypy enforcement.
- **Ruff/flake8 config** — no linting rules beyond pytest basics in `pyproject.toml`. Adding ruff would let us enforce import ordering and ban cross-layer imports programmatically.
- **Deepening shallow modules** — several modules have interfaces nearly as complex as their implementations. Tracked via architecture review process.
