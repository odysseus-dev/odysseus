# Coding Conventions

**Analysis Date:** 2026-06-03

Odysseus is a Python 3.11+ FastAPI backend with a vanilla-JS (ES module) frontend in `static/js/`. There is no autoformatter or linter config in the repo (no `.eslintrc`, `.prettierrc`, `ruff.toml`, `black`, or `.editorconfig`). Conventions are enforced by review and by consistency with existing code, not by tooling. Match the surrounding file.

## Naming Patterns

**Files (Python):**
- `snake_case.py` everywhere — no uppercase letters in module names. Examples: `src/agent_loop.py`, `routes/chat_routes.py`, `core/atomic_io.py`.
- Layer is encoded in the directory, not the filename: `routes/*_routes.py` for HTTP routers, `routes/*_helpers.py` for route-support logic, `services/<name>/*.py` for self-contained subsystems, `src/*.py` for core domain logic, `core/*.py` for cross-cutting infra (auth, DB, middleware).

**Files (JavaScript):**
- `camelCase.js` or lowercase module names under `static/js/` (e.g. `static/js/markdown.js`, `static/js/calendar/utils.js`). ES modules with `import`/`export`.

**Functions / variables:**
- `snake_case` for all Python functions and variables.
- Leading underscore (`_helper`) marks module-private / internal helpers. This is heavily used — e.g. `_parse_anthropic_response` (`src/llm_core.py`), `_detect_admin_intent` (`src/agent_loop.py`), `_request_values` (`routes/search_routes.py`). Tests import these private functions directly to unit-test them.
- JS internal helpers also use a leading underscore (`_addDays`, `_shiftDT` in `static/js/calendar/utils.js`).

**Types / classes:**
- `PascalCase`. Custom exceptions end in `Error` (`SessionNotFoundError`, `LLMServiceError` in `core/exceptions.py`). SQLAlchemy models are PascalCase nouns (`Session`, `ChatMessage`, `EmailAccount` in `core/database.py`). Mixins end in `Mixin` (`TimestampMixin`).

**Constants:**
- `UPPER_SNAKE_CASE` at module top (`PROVIDER_INFO`, `DATA_DIR`, `DIRS`).

## Code Style

**Formatting:**
- No autoformatter. ~4-space indentation, double-quoted strings predominate. Keep lines reasonably short but no hard column limit is enforced.
- Trailing commas in multi-line literals/calls are common (e.g. the `comprehensive_web_search(query, return_sources=True, time_filter=time_filter,)` call in `routes/search_routes.py`).
- Section banners using a comment rule are used in larger files and tests to group related code:
  ```python
  # ---------------------------------------------------------------------------
  # _detect_admin_intent
  # ---------------------------------------------------------------------------
  ```

**Linting:**
- None configured. The only mechanical check used in development is `python -m py_compile` (see CONTRIBUTING.md) and `node --check` for JS files.

**Type hints:**
- Used on public function signatures, especially route handlers and helpers: `async def _request_values(request: Request) -> Dict[str, Any]:`. Return types like `-> APIRouter`, `-> Dict[str, Any]`, `-> bool` are common.
- `from __future__ import annotations` is used selectively (~9 files, e.g. `core/atomic_io.py`), not project-wide. Don't assume deferred annotations.
- Typing is pragmatic, not strict — many large modules are partially typed. Add hints to new public functions; match the file for internals.

## Import Organization

**Order (observed convention):**
1. `from __future__ import annotations` (when used), at the very top after the docstring.
2. Standard library (`import logging`, `import os`, `import json`, `import time`).
3. Third-party (`from fastapi import APIRouter, Request`, `import pytest`).
4. First-party / local (`from services.search import ...`, `from src.llm_core import ...`, `from core.database import ...`).

Groups are separated by a blank line. See `routes/search_routes.py` for a clean example.

**Path layout (import roots):**
- Project root is on `sys.path`; imports are absolute from the top-level packages: `src.`, `routes.`, `services.`, `core.`. No path aliases.
- `services/<name>/__init__.py` is a barrel that re-exports the public surface and defines `__all__` (see `services/search/__init__.py`). Import from the package, not the submodule, for public API: `from services.search import comprehensive_web_search`.

## Error Handling

**Patterns:**
- Custom domain exceptions live in `core/exceptions.py` (and `src/exceptions.py`), each subclassing `Exception`, storing context attributes, and calling `super().__init__(message)`.
- Route handlers favor **degrade-gracefully over raise**: endpoints catch broad `except Exception as e`, log the error, and return a structured payload with an `"error"` key rather than throwing a 500. Example (`routes/search_routes.py`):
  ```python
  try:
      results = _call_provider(provider, query, min(count, 20))
      return {"results": results, "provider": provider, "time": elapsed}
  except Exception as e:
      logger.error(f"Search provider {provider} failed: {e}")
      return {"results": [], "provider": provider, "time": elapsed, "error": str(e)}
  ```
- Input parsing is forgiving: handlers accept JSON, form data, or query params (`_request_values`), coerce with `str(...)`/`int(...)` inside `try`/`except`, and fall back to defaults rather than 422-ing.
- Durability matters: any JSON config/state file MUST be written via `core/atomic_io.py` (`atomic_write_json` / `atomic_write_text`) — never raw `open("w") + json.dump`. The tmp-file + fsync + `os.replace` pattern prevents truncation on crash. This applies to `auth.json`, `sessions.json`, `settings.json`, `integrations.json`, `cookbook_state.json`.

## Logging

**Framework:** stdlib `logging`. Every module that logs declares `logger = logging.getLogger(__name__)` at module top (used in ~117 files). No print() in library code (CLI/setup scripts excepted).

**Patterns:**
- f-string interpolation is the house style for log messages (~400+ occurrences): `logger.error(f"Standalone web search failed: {e}")`. The lazy `%`-style is NOT the convention here — match the f-string form.
- Levels: `logger.error(...)` for caught exceptions in handlers, `logger.warning(...)` for recoverable/degraded paths, `logger.info(...)`/`logger.debug(...)` for flow.

## Comments

**When to Comment:**
- Comments explain *why*, not *what* — frequently citing the bug or scenario that motivated the code. Example from `routes/search_routes.py`: the FastAPI `Form(...)` vs JSON 422 rationale. Module docstrings often name the consuming file and the failure mode being guarded against (`core/atomic_io.py`).
- Inline `# Regression:` / `# the bug this PR fixes` notes are common in both code and tests to pin down intent.

**Docstrings:**
- Module-level docstring at the top of nearly every file, one line minimum, often a paragraph explaining purpose and dependencies.
- Function docstrings on public functions and non-obvious helpers; terse imperative style. No enforced docstring format (not Google/NumPy style — freeform prose).

## Function Design

**Size:** Helpers are small and single-purpose. Note that several core modules are very large (`src/tool_implementations.py` ~183 KB, `src/task_scheduler.py` ~104 KB, `routes/email_routes.py` ~151 KB) — these are existing concentrations, not a target to emulate. Prefer extracting new logic into focused helpers.

**Parameters:**
- Keyword-only args after `*` are used to force clarity on optional behavior: `def atomic_write_json(path: str, data: Any, *, indent: Optional[int] = None)`.
- Defaults are simple immutables; optional values default to `None` and are normalized inside the function.

**Return Values:**
- Route handlers return plain `dict` / list payloads (FastAPI serializes to JSON). Error responses are dicts with an `"error"` key, not exceptions.
- Helpers return concrete types matching their hint; predicates return `bool` and are tested with `is True` / `is False`.

## Module Design

**Routers:**
- HTTP routers are built by a `setup_<area>_routes(...)` factory that takes its dependencies as arguments, creates `APIRouter(prefix="/api/...", tags=[...])`, defines handlers as nested `async def`, and returns the router. See `routes/search_routes.py`, `routes/api_token_routes.py`, `routes/auth_routes.py`. Dependencies (e.g. `auth_manager`, `session_manager`, `task_scheduler`) are injected via the factory, not imported globally — this keeps routes testable and wired in `app.py`.

**Exports:**
- `services/` subpackages expose a curated public API via `__init__.py` re-exports + `__all__`. Submodules keep internals private with leading underscores.

**Barrel files:**
- Used at the `services/<name>/__init__.py` level. Not used for `src/` or `routes/` — import those modules directly.

**Frontend modules:**
- `static/js/` uses native ES modules with explicit `import { x } from './path.js'` and `export function` / `export const`. No bundler step; files are served as-is, so imports use real relative paths with `.js` extensions.

---

*Convention analysis: 2026-06-03*
