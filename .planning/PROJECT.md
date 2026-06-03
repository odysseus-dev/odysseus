# Odysseus — Engineering Modernization

## What This Is

A behavior-preserving modernization of **Odysseus**, a self-hosted, local-first AI assistant/agent platform (FastAPI backend, vanilla-JS SPA, streaming agent loop with tool-calling, RAG, email/calendar/contacts, image generation, MCP support). This milestone restructures the codebase to modern software-engineering standards — breaking up god-files, adding type safety, reducing global state, and adding automated quality gates — and runs a systematic security audit, **without changing the product's features, HTTP/SSE APIs, or UX**. For the maintainers and contributors of Odysseus.

## Core Value

The application behaves **identically** after the work — every existing feature and API still works, proven by the existing test suite — while the code underneath is materially easier to change, safer, and enforceably clean.

## Requirements

### Validated

<!-- Existing, shipped capabilities inferred from the codebase map. These must keep working unchanged. -->

- ✓ Streaming agent loop with multi-round LLM tool-calling (`src/agent_loop.py`, `src/llm_core.py`) — existing
- ✓ Provider-agnostic LLM core (Ollama/vLLM/OpenAI/Anthropic/OpenRouter) with retry + fallback — existing
- ✓ Native + MCP tool system (parse → schema → dispatch → implement) — existing
- ✓ RAG over ChromaDB with local-embedding fallback (`src/tool_index.py`, `src/embeddings.py`) — existing
- ✓ Email (IMAP/SMTP), calendar (CalDAV/ICS), contacts integrations — existing
- ✓ Image generation, gallery, document processing, research reports — existing
- ✓ Cookbook, tasks/scheduler, event bus, background automation — existing
- ✓ Auth (cookie session + API token, bcrypt, TOTP 2FA), per-owner row scoping, security headers — existing
- ✓ SQLAlchemy/SQLite persistence (24 tables) + JSON state files + ChromaDB vector stores — existing
- ✓ Self-host deployment (Docker Compose, macOS/Windows/Linux launchers) — existing

### Active

<!-- This milestone's scope. All behavior-preserving. -->

- [ ] Establish an objective baseline **scorecard** (module sizes, type coverage, lint findings, security findings, performance baseline, test-coverage map) with target thresholds that define "done"
- [ ] Break up god-files into focused modules following the existing `*_helpers.py` / `setup_*_routes` convention (`src/tool_implementations.py` ~4100 lines, `routes/email_routes.py` ~3200, `routes/cookbook_routes.py`, `routes/model_routes.py`, `routes/gallery_routes.py`)
- [ ] Add modern type hints (`list[X]`, `X | None`) and enforce with **mypy**
- [ ] Reduce module-level singletons and implicit setter-wiring (`set_session_manager`, `set_task_scheduler`, …) in favor of explicit dependency injection
- [ ] Adopt **ruff** lint + format and enforce both (plus the quality gates above) in **CI**
- [ ] Modernize the frontend SPA (`static/`, 65 vanilla-JS modules) toward the same maintainability bar
- [ ] Harden the data/migration layer (`core/database.py`): 37 hand-rolled additive migrations, SQLite pragmas/WAL/busy_timeout, ORM layer
- [ ] Pin dependencies and add a lockfile (`requirements.txt` is currently unpinned)
- [ ] Run a systematic **security audit** (auth, input handling, secret storage, dependencies) and close findings, building on `THREAT_MODEL.md` and the security-regression suite
- [ ] Replace deprecated APIs (e.g. 24 files using `datetime.utcnow()`)

### Out of Scope

<!-- Explicit boundaries. -->

- **Feature changes / new functionality** — this is a modernization milestone, not a feature milestone
- **API / SSE contract changes** — HTTP and streaming surfaces stay identical so clients/UX are unaffected
- **UX / visual redesign** — the SPA's behavior and appearance are preserved; only internal structure changes
- **Database schema redesign / data migration** — table structure stays compatible; migration *mechanism* may be hardened but existing data must continue to load
- **Full frontend rewrite / framework adoption** — no React/Vue/build-step migration; stay vanilla-JS, improve organization only
- **Performance re-architecture** — performance is a guardrail (no regressions, capture obvious wins), not a driver for structural change

## Context

- **Brownfield, mature codebase.** Full analysis lives in `.planning/codebase/` (STACK, ARCHITECTURE, STRUCTURE, CONVENTIONS, TESTING, INTEGRATIONS, CONCERNS), refreshed 2026-06-03.
- **Established conventions exist.** The codebase already follows a modular-monolith pattern: `setup_<domain>_routes(deps) -> APIRouter` factories, constructor DI via `src/app_initializer.initialize_managers()`, facade + split-module refactors (`agent_tools.py` re-exports four submodules), and `*_helpers.py` companions. Modernization should *extend these existing patterns*, not impose new ones.
- **Known concern hot-spots** (from `CONCERNS.md`): god-files; SQLite concurrency (`check_same_thread=False` with email pollers + scheduler — verify WAL/busy_timeout actually configured, sources conflict); 37 hand-rolled migrations re-scanned each startup; unpinned deps / no lockfile; encryption key co-located with data (`src/secret_storage.py`); deprecated `datetime.utcnow()` in 24 files (clustered in calendar/scheduler, already a source of UTC regressions); two recent reverts still on `main` (`67b63e9`, `1f6c5ac`) worth checking for orphaned code.
- **Security-conscious baseline.** Dedicated `THREAT_MODEL.md`, `SECURITY.md`, `tests/test_security_regressions.py`, per-owner scoping, Fernet-at-rest secrets. The audit *builds on* this rather than starting cold.
- **Test suite as the contract.** 355 test files (pytest + pytest-asyncio, `asyncio_mode=auto`). A green suite is the agreed signal that behavior is preserved. Note: large surfaces like `src/tool_implementations.py` and `routes/email_routes.py` have only helper-level / indirect coverage — coverage gaps must be filled *before* refactoring those areas.

## Constraints

- **Behavior**: No change to features, HTTP/SSE API contracts, or UX — every refactor must be verifiable against existing behavior via the test suite.
- **Verification**: The existing pytest suite is the primary "nothing broke" signal; areas with thin coverage get tests added before they are refactored.
- **Tech stack**: Stay on the current stack — Python 3.12, FastAPI/Uvicorn, SQLAlchemy/SQLite, vanilla-JS frontend (no build step / framework), Docker Compose. No framework migrations.
- **Conventions**: Extend the codebase's existing patterns (route factories, `initialize_managers` DI, helper-split, optional-subsystem graceful degradation) rather than introducing competing ones.
- **Performance**: No regressions; opportunistic wins only — performance is not a justification for behavior or contract changes.
- **Goals are co-equal**: maintainability, security, and performance-as-guardrail are weighted together; the audit scorecard balances them rather than optimizing one at others' expense.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Behavior-preserving refactor (no feature/API/UX change) | Mature working app; lowest-risk path; every change verifiable against existing behavior | — Pending |
| Audit-driven "done" via a baseline scorecard + thresholds | Open-ended refactors need an objective stopping condition; assess → set targets → refactor to threshold | — Pending |
| Lean on the existing 355-file test suite as the behavior contract | User trusts the suite; passing tests = behavior preserved | — Pending |
| Extend existing conventions, don't replace them | The codebase already has good patterns (route factories, DI, helper-split); consistency over novelty | — Pending |
| Treat performance as a guardrail, not a driver | No felt performance pain reported; structural change shouldn't be justified by speculative perf | — Pending |
| Fill coverage gaps before refactoring thinly-tested god-files | `tool_implementations.py` / `email_routes.py` lack direct tests; refactoring them blind is unsafe | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-03 after initialization*
