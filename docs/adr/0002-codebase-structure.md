# ADR-0002: Codebase Structure and Layer Conventions

**Status:** Accepted
**Date:** 2026-06-01
**Supersedes:** None (complements ADR-0001)

## Context

Odysseus has grown organically from a single-file prototype into 400+ files across Python backend, vanilla JS frontend, and CLI scripts. This ADR codifies the layer conventions that have emerged, so future changes (human or agent) have a shared reference. Only rules we can enforce today are included — aspirational deepening is tracked separately.

## Decision

### Directory Layers

Five directories with defined responsibilities:

| Layer | Path | Responsibility | May import from |
|-------|------|---------------|-----------------|
| **Foundation** | `core/` | Auth, database models, session persistence, middleware, pure data containers | Standard library, third-party packages, other `core/` modules |
| **Business Logic** | `src/` | LLM core, agent loop, chat processing, memory, tools, model context | `core/`, standard library, third-party packages, other `src/` modules |
| **Routes** | `routes/` | FastAPI route modules — one file per feature domain | `core/`, `src/`, other `routes/` helpers |
| **Services** | `services/` | Feature services with their own sub-packages (memory, search, research, hwfit, etc.) | `core/`, `src/`, same service package |
| **Scripts** | `scripts/` | CLI entry points (`odysseus-*`) | `core/`, `src/`, `services/` |

### Import Rules

1. **No upward imports.** A layer may only import from layers below it (per the table above). Specifically:
   - `core/` must NOT import from `src/`, `routes/`, or `services/`
   - `src/` must NOT import from `routes/` or `services/`
   - `routes/` may import from `core/` and `src/`

2. **No cross-service imports between `services/` sub-packages.** `services/memory/` must not import from `services/search/`. Shared logic belongs in `src/` or `core/`.

3. **No duplication of modules across `src/` and `services/`.** If a module exists in both locations, pick one home and remove the other. When in doubt, `src/` for shared business logic, `services/` for feature-specific implementation.

### Naming Conventions

- **Routes:** One file per domain in `routes/` — `{domain}_routes.py`. Helpers live as `{domain}_helpers.py` in the same directory.
- **Tests:** `tests/test_{module_name}.py` — mirrors the source module being tested.
- **Frontend modules:** `static/js/{feature}.js` — one file per feature area, vanilla JS IIFE/module pattern (no ES modules, no framework).

### Owner Scope Isolation

All user data is isolated by owner ID. Every query that touches user-owned data (sessions, messages, documents, memories, emails) must filter by owner. Null owners indicate admin-level shared resources. This is enforced in `core/session_manager.py` and checked at route level via `src/auth_helpers`.

## Consequences

### Immediate (enforceable with grep/pre-commit)

- `grep -r "from src\|import src" core/` — should return zero results
- `grep -r "from routes\|import routes" src/ services/` — should return zero results
- `ls src/*.py services/*/*.py | xargs basename | sort | uniq -d` — should return zero duplicates

### Known Violations (tracked, not blocking)

| Rule | Violation | Resolution PR |
|------|-----------|---------------|
| No upward imports | `core/__init__.py` imports from `src.llm_core` | Move `llm_core` re-exports out of `core/__init__.py` — callers should import directly from `src/` |
| No upward imports | `src/app_initializer.py` imports from `services.memory.skills` | Move `SkillsManager` to `src/` or make it a `src/` facade |
| No duplication | `memory.py`, `memory_vector.py`, `research_handler.py`, `youtube_handler.py` exist in both `src/` and `services/` | Audit each pair, pick home, remove duplicate — one domain per PR |
| No duplication | `search/` directory fully duplicated between `src/search/` and `services/search/` with diverging implementations | Consolidate to `services/search/` (routes import from there), remove `src/search/` |

### Future Work (not in scope)

- **Type annotations** — most functions lack them. Would enable mypy enforcement.
- **Ruff/flake8 config** — no linting rules beyond pytest basics in `pyproject.toml`. Adding ruff would let us enforce import ordering and ban cross-layer imports programmatically.
- **Deepening shallow modules** — several modules have interfaces nearly as complex as their implementations. Tracked via architecture review process (see `improve-codebase-architecture` skill).

## Implementation Plan

Split into separate PRs, each independently mergeable:

1. **Docs PR** (this ADR) — establishes the rules
2. **Backend layer conventions** — fix `core/__init__.py` imports, add ruff config with import bans
3. **Frontend API/query conventions** — document fetch patterns, error handling, SSE consumption
4. **Test/tooling baseline** — add ruff to pre-commit, configure mypy (allow-untyped-defs initially)
5. **File moves** — one domain at a time (search consolidation, memory dedup, etc.)
