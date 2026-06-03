# Codebase Concerns

**Analysis Date:** 2026-06-03

> Odysseus is a self-hosted FastAPI AI workspace (~114K lines of Python across 560 files, plus ~109K lines of frontend JS). It is unusually well-tested for its size (355 test files, including a dedicated `tests/test_security_regressions.py` that pins prior fixes). The security model is explicit and documented (`THREAT_MODEL.md`, `SECURITY.md`): it targets *trusted users on a private network*, and privileged capabilities (shell, file I/O, email) are intentional admin features, not vulnerabilities. The concerns below are therefore about maintainability, concurrency, and at-rest hardening rather than gaping holes.

## Tech Debt

**Monolithic source files:**
- Issue: Several files have grown well past a maintainable size and mix many responsibilities, making them hard to navigate, review, and test in isolation.
- Files: `src/tool_implementations.py` (4144 lines), `routes/email_routes.py` (3214 lines), `src/agent_loop.py` (2300 lines), `src/task_scheduler.py` (2255 lines), `src/builtin_actions.py` (2237 lines), `routes/cookbook_routes.py` (2207 lines), `core/database.py` (1895 lines), `src/visual_report.py` (1885 lines), `routes/gallery_routes.py` (1807 lines), `routes/model_routes.py` (1802 lines). Frontend equivalents: `static/js/document.js` (9731 lines), `static/js/slashCommands.js` (6099 lines), `static/js/emailLibrary.js` (5217 lines), `static/js/notes.js` (5009 lines).
- Impact: High cognitive load for any change; merge conflicts; difficult to unit-test individual concerns; slow editor/tooling performance.
- Fix approach: Split by responsibility. `src/tool_implementations.py` can be decomposed per tool family (search, email, calendar, shell, etc.) into a package. `routes/*.py` route modules can extract helper/business logic into `src/` services (the pattern already exists, e.g. `routes/email_helpers.py`, `routes/chat_helpers.py`). Do this incrementally, guarded by the existing test suite.

**Hand-rolled, additive-only migration system:**
- Issue: There is no migration framework (Alembic, etc.). Schema evolution is implemented as 37 bespoke `_migrate_*` functions in `core/database.py`, each opening a raw `sqlite3` connection, running `PRAGMA table_info(...)`, and conditionally issuing `ALTER TABLE ... ADD COLUMN`.
- Files: `core/database.py` (37 `_migrate_*` functions, 21 raw `sqlite3.connect` call sites).
- Impact: Migrations run on every startup and re-scan every table. They are additive-only — no column drops/renames/type changes are expressible without bespoke table-rebuild code (see `scripts/update_database.py`). The list grows unbounded and is easy to get subtly wrong (ordering, idempotency). Each migration duplicates the connect/inspect/alter boilerplate.
- Fix approach: Introduce Alembic (SQLAlchemy is already the ORM) with a versioned migration chain, or at minimum a shared migration helper that removes the per-function boilerplate and records applied versions in a `schema_migrations` table so startup doesn't re-scan everything.

**Deprecated `datetime.utcnow()`:**
- Issue: `datetime.utcnow()` is deprecated in Python 3.12+ and returns naive datetimes, which has historically caused timezone bugs in this codebase (see git history: multiple calendar/scheduler UTC fixes).
- Files: 24 source files including `src/task_scheduler.py`, `src/cleanup_service.py`, `src/caldav_sync.py`, `src/webhook_manager.py`, `src/ai_interaction.py`, `routes/email_pollers.py:983`.
- Impact: Deprecation warnings now; behavior change / removal in a future Python; naive-vs-aware datetime mismatches around scheduling and recurrence (an area that has already produced several regressions).
- Fix approach: Replace with `datetime.now(timezone.utc)` consistently, and audit downstream comparisons/serialization for naive/aware mixing.

## Known Bugs

No open bugs are tracked in-repo (only 3 `TODO`/`FIXME` markers exist across the entire codebase, none describing active defects). The git history instead shows a steady stream of *fixed* edge-case bugs that have been pinned with regression tests, which is the project's bug-management style rather than a backlog of known-broken behavior.

**Historical hot spots (recently fixed, watch for regressions):**
- Calendar recurrence / UTC handling — e.g. `bfbbc9b fix(calendar): keep recurring events with a UTC UNTIL from collapsing to one (#1383)`. Files: `routes/calendar_routes.py`, `static/js/calendar/`.
- Email parsing edge cases — MIME charset (`e678ff7 #1354`), SMTP envelope recipients splitting on commas in display names (`f192657 #1464`), PDF attachment `lstrip` eating body text (`0dd6714 #1541`). Files: `routes/email_routes.py`, `routes/email_helpers.py`, `src/email_thread_parser.py`.
- Search ranking false-positive substring matches — `b55c970 fix: sports-hint ranking penalty fires on 'transport'/'passport' substrings (#1473)`, `fb8a744 fix: skill retrieval boosts on tag substrings (#1406)`. Files: `src/search/`, `services/search/`, `services/memory/skill_extractor.py`.
- Reverted features still on `main`: `67b63e9 Revert "fix(ui): allow manual prompt bar resize"` and `1f6c5ac Revert "Codex Agent integration..."` — confirm no orphaned/dead code remains from these reverts.

## Security Considerations

> Per `THREAT_MODEL.md`, shell/Python execution, file read/write, and email send/read are **intentional admin capabilities** for trusted local users. They are not listed below as risks. The items below are residual at-rest and injection hardening concerns.

**Encryption key co-located with encrypted data:**
- Risk: API keys and email credentials are encrypted at rest with Fernet, but the key lives next to the database it protects.
- Files: `src/secret_storage.py:32` (`_KEY_PATH = .../data/.app_key`), `core/database.py` (`EncryptedText` TypeDecorator). The DB file is also under `data/`.
- Current mitigation: Key file is `chmod 0o600` (POSIX); code comments are explicit that this protects a *stolen backup / leaked image*, not a live process or an attacker who reads `data/`. `decrypt()` fail-soft returns `""` on a bad/rotated key so a corrupt row degrades rather than 500s.
- Recommendations: Document that backups must exclude `data/.app_key` (or that including it negates encryption). Consider supporting an env-var / external KMS key source so the key need not sit on disk beside the ciphertext for higher-stakes deployments.

**Prompt injection via untrusted content:**
- Risk: The agent ingests web results, fetched pages, emails, and memories — all of which can contain adversarial instructions. This is called out as an explicit threat in `THREAT_MODEL.md`.
- Files: `src/prompt_security.py` (`untrusted_context_message`, `UNTRUSTED_CONTEXT_POLICY`), and consumers `src/agent_loop.py`, `src/tool_execution.py`, `src/chat_processor.py`, `src/integrations.py`, `src/upload_handler.py`.
- Current mitigation: Untrusted content is wrapped as a non-system data role and labeled by policy (pinned in `tests/test_security_regressions.py`). Non-admins are blocked from high-risk tools (`src/tool_security.py:NON_ADMIN_BLOCKED_TOOLS`).
- Recommendations: Continue expanding regression coverage as new ingestion surfaces are added; ensure every new content source routes through `prompt_security` wrapping rather than concatenating raw text into the prompt.

**Dynamic SQL via f-string interpolation:**
- Risk: A number of queries build SQL with f-strings rather than bound parameters.
- Files: `core/database.py` (lines 968, 971, 972, 1069, 1072, 1169, 1196, 1289, 1641, 1675), `mcp_servers/email_server.py:75`, `routes/email_pollers.py:983`, `routes/task_routes.py:473-474`, `scripts/update_database.py:38,67,68`.
- Current mitigation: In every observed case the interpolated values are *schema-derived* (table names, column names from `PRAGMA table_info`, or a fixed allow-list of column specs), not user input. Row values are still passed as bound `?`/`:name` parameters.
- Recommendations: Keep interpolated identifiers strictly to internal constants/schema introspection (never request data). Add a comment at each site noting the value source, and consider a small `_safe_ident()` allow-list helper to make the invariant enforceable rather than convention.

## Performance Bottlenecks

**Startup re-scans all tables on every boot:**
- Problem: 37 `_migrate_*` functions each open a raw connection and run `PRAGMA table_info` (and sometimes full-table backfills) at startup.
- Files: `core/database.py` (e.g. `_migrate_add_last_message_at`, `_migrate_add_document_archived_column`, and 35 others).
- Cause: No applied-migration ledger; idempotency is achieved by re-inspecting schema each time instead of recording what already ran.
- Improvement path: Record applied migrations and short-circuit. This also bounds startup time as the schema grows.

**Large frontend bundles served as monolithic files:**
- Problem: Single JS files up to ~9700 lines are loaded by the browser.
- Files: `static/js/document.js`, `static/js/slashCommands.js`, `static/js/emailLibrary.js`, `static/js/notes.js`, `static/js/chat.js`.
- Cause: No bundler/code-splitting (the project has only a minimal Node `package.json`; JS is hand-authored and statically served).
- Improvement path: Code-split per feature surface and lazy-load; or adopt a lightweight bundler. Lower priority given the self-hosted, single-user-ish deployment model.

## Fragile Areas

**SQLite under concurrent async access (no WAL / no busy_timeout):**
- Files: `core/database.py:30` (`engine = create_engine(..., connect_args={"check_same_thread": False})`), plus 21 ad-hoc `sqlite3.connect()` sites across `core/database.py`, `mcp_servers/email_server.py`, `routes/email_pollers.py`, `routes/task_routes.py`.
- Why fragile: The app is async (FastAPI/uvicorn) with background pollers (email), a task scheduler, and cleanup services all hitting the same SQLite file. `check_same_thread=False` is set, but there is **no `PRAGMA journal_mode=WAL` and no `busy_timeout`** configured (only `foreign_keys=ON` is set in the `connect` event listener). Under concurrent writes this risks `database is locked` (`OperationalError`) — and several code paths catch `sqlite3.OperationalError` and silently degrade (e.g. `routes/task_routes.py:476` sets count to 0), which can mask lost work.
- Safe modification: Add `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=<ms>` in the `set_sqlite_pragma` listener (`core/database.py:46`) and on the raw `sqlite3.connect` helpers. Route raw connections through a single shared helper instead of 21 inline `sqlite3.connect` calls.
- Test coverage: Concurrency under lock contention is not exercised (`test_scheduler_restart_doublefire.py` covers scheduling logic, not DB contention).

**Module-level singleton wiring in `app.py`:**
- Files: `app.py` (e.g. `auth_manager`, `rag_manager`, `webhook_manager`, `tts_service`, `stt_service`, `components = initialize_managers(...)` at lines 154-596), and cached globals such as `src/agent_loop.py:444` (`_cached_base_prompt`, mutated via `global` at line 556).
- Why fragile: Heavy import-time side effects and process-wide mutable state make ordering significant, complicate testing (the test suite works around this by importing modules lazily inside test functions — see `tests/test_security_regressions.py`), and make it hard to run multiple isolated app instances.
- Safe modification: Prefer dependency injection / factory functions and lazy initialization; avoid adding new import-time work in `app.py`. Treat the existing lazy-import test pattern as the contract.

**Broad exception swallowing:**
- Files: codebase-wide — 1104 `except Exception` handlers and ~250 `except ...: pass` blocks across `src/`, `routes/`, `core/`.
- Why fragile: Some are deliberate fail-soft degradation (a documented pattern, e.g. `secret_storage.decrypt`, settings loading `398892c #1570`), but blanket `except Exception` can hide real defects and turn bugs into silent no-ops. Several DB error paths swallow `OperationalError` and report zeroes.
- Safe modification: When touching these areas, narrow the caught exception type and at minimum log at WARNING with context. Distinguish intentional degradation (keep, comment it) from accidental swallowing (fix).

## Scaling Limits

**Single-file SQLite as the system of record:**
- Current capacity: Adequate for the intended single-host, trusted-user deployment.
- Limit: Concurrent-writer throughput (pollers + scheduler + interactive requests) is bounded by SQLite's single-writer model, exacerbated by the missing WAL/busy_timeout config above.
- Scaling path: Enable WAL first (cheap win). For genuine multi-user/high-concurrency, the SQLAlchemy abstraction makes Postgres feasible — but the 21 raw `sqlite3.connect()` call sites and `sqlite3`-specific code (`PRAGMA`, `sqlite3.OperationalError`, `sqlite3.Row`) are hard-coupled to SQLite and would all need to move through the ORM/engine first.

## Dependencies at Risk

**Unpinned Python dependencies:**
- Risk: `requirements.txt` lists packages with **no version pins** (e.g. `fastapi`, `uvicorn`, `pydantic>=2.0`, `SQLAlchemy`, `chromadb-client`, `fastembed`, `mcp`, `cryptography`, `bcrypt`). There is no `requirements.lock` / hash file.
- Impact: Non-reproducible builds; a transitive or upstream release can silently break or alter behavior (e.g. a pydantic or SQLAlchemy minor bump). `chromadb-client`/`fastembed` and the `mcp` package are fast-moving.
- Migration plan: Pin exact versions (or compatible-release `~=`) and commit a lockfile. CI should install from the lock to catch drift. (Node side already commits `package-lock.json`.)

**ML/RAG stack is heavyweight and optional-degrading:**
- Risk: `chromadb-client` + `fastembed` (ONNX embeddings) are on the core agent path but the app advertises a keyword fallback if they're missing.
- Impact: The "degrades gracefully" path is a second code path that can rot if not exercised; embedding/vector behavior differs silently between full and fallback installs.
- Migration plan: Add a smoke test for the keyword-fallback path so the degraded mode stays correct.

## Missing Critical Features

**No applied-migration ledger / downgrade path:**
- Problem: Schema changes are additive-only and unversioned (see Tech Debt). There is no way to roll back a migration or know which migrations a given DB has seen.
- Blocks: Safe schema *changes* (renames, type changes, drops) and confident upgrade/rollback during releases.

**No release/version pinning discipline for dependencies:**
- Problem: See Dependencies at Risk. `SECURITY.md` notes "no formal releases are cut" — fixes ship on the default branch.
- Blocks: Reproducible deployments and clear "what version am I running" for security response.

## Test Coverage Gaps

> The suite is large (355 test files) and security/edge-case focused. Gaps below are the biggest *untested* surfaces relative to their size/risk.

**Largest source files lack dedicated tests:**
- What's not tested: No `tests/test_tool_implementations*.py` exists for `src/tool_implementations.py` (4144 lines, the central tool dispatch surface). No `tests/test_email_routes*.py` for `routes/email_routes.py` (3214 lines) — only helper-level tests (`tests/test_*email*` cover parsing/pollers/helpers).
- Files: `src/tool_implementations.py`, `routes/email_routes.py`.
- Risk: Regressions in tool dispatch or email route handling could slip through; coverage is concentrated in helpers and parsers rather than the orchestrating modules.
- Priority: High (`tool_implementations.py` is the agent's primary action surface).

**DB concurrency / lock contention untested:**
- What's not tested: Concurrent writers against SQLite without WAL (the fragile area above). Existing scheduler tests cover logic, not contention.
- Files: `core/database.py`, `routes/email_pollers.py`, `src/task_scheduler.py`.
- Risk: `database is locked` failures and silently-swallowed `OperationalError` paths go unnoticed until production load.
- Priority: Medium-High.

**Keyword fallback path (no Chroma/fastembed) untested:**
- What's not tested: Behavior when the optional vector stack is absent and the app degrades to keyword search/memory.
- Files: `src/search/`, `services/search/`, `services/memory/`.
- Risk: The advertised graceful-degradation mode can break unnoticed since CI presumably installs the full stack.
- Priority: Medium.

---

*Concerns audit: 2026-06-03*
