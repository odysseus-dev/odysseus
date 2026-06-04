# Persistence

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers durable state in:

- `core/database.py`;
- `src/database.py`;
- `core/models.py`;
- `core/session_manager.py`;
- `core/atomic_io.py`;
- JSON stores managed by `core/auth.py`, `src/settings.py`, `src/api_key_manager.py`, `src/preset_manager.py`, `src/integrations.py`, `src/upload_handler.py`, `src/personal_docs.py`, `src/research_handler.py`, `src/bg_jobs.py`, `routes/prefs_routes.py`, `routes/contacts_routes.py`, `routes/vault_routes.py`, `routes/cookbook_routes.py`, and memory/skills managers;
- `routes/email_helpers.py` scheduled-email storage;
- `routes/backup_routes.py` and `scripts/odysseus-backup`;
- runtime data under `data/`.

## Database Shape

`core/database.py` owns SQLAlchemy models and startup migrations. `src/database.py` is a compatibility re-export for legacy imports. Route and service code commonly owns its own `SessionLocal()` lifecycle instead of using one central unit-of-work wrapper.

The default database is SQLite at `data/app.db`. SQLAlchemy can point at a non-SQLite `DATABASE_URL`, but current startup migrations/backfills are SQLite-first and often use `sqlite3`, `PRAGMA`, or SQLite catalog queries. External DBs are not fully migration-compatible unless those helpers are made backend-neutral.

Timestamp defaults use `utcnow_naive()` so existing naive `DateTime` columns stay UTC without the deprecated `datetime.utcnow()` default.

Current model families include:

- chat sessions, messages, and `chat_messages_fts` transcript-search state/triggers;
- documents and document versions;
- gallery albums/images, editor drafts, signatures, generated-media metadata;
- email accounts, model endpoints, MCP servers, comparisons;
- API tokens, admin-global webhooks, user tools/tool data, integrations;
- crew members, scheduled tasks, task runs, notes;
- memory rows, calendar calendars, and calendar events.

`core/models.py` owns pure dataclasses used by `SessionManager`. It does not own database persistence.

`routes/email_helpers.py` owns a second SQLite database at `data/scheduled_emails.db` for scheduled email, summary, reply, tag, and cache state. Its migrations and owner backfills are local to that module, not `core/database.py`.

## Migration Policy

Odysseus does not use Alembic. `core.database.init_db()` runs at module import, before FastAPI lifespan startup. `Base.metadata.create_all()` creates missing tables; hand-written `_migrate_*` functions add or reshape legacy columns.

Runtime behavior:

- migrations must be idempotent;
- SQLite foreign keys are enabled for every engine connection;
- new SQLAlchemy columns need matching startup migration code;
- legacy ownerless/shared rows may exist and must be handled by owner-aware route helpers.

Startup backfills include document-owner backfill from linked sessions, blanket legacy owner assignment for SQL and selected JSON stores, `user_prefs.json` per-user nesting, email account seeding from legacy settings, and encryption rewrites for legacy plaintext endpoint, signature, and email secrets. Failed encryption rewrites are logged and retried on later startup.

## Ownership And Access

Owner columns are security-relevant. Current owner-bearing domains include sessions, documents, gallery images/albums, editor drafts, model endpoints, signatures, API tokens, user tools/tool data, comparisons, crew members, scheduled tasks/task runs, memories, notes, calendars/events, email accounts, and integrations. Webhooks are admin-global today and do not have an owner column.

Route code owns filtering for its domain. `src.auth_helpers.owner_filter()` is the common helper where available; gallery, documents, calendar, email, skills, and other surfaces also use local filters. Null-owner compatibility is domain-specific: shared endpoints may include null owners, while strict gates and disk stores may reject them. Do not rely on frontend filtering for access control.

## Secrets And Local Stores

`ModelEndpoint` includes cached/hidden/pinned model lists, endpoint kind, refresh mode/interval/timeout, model type, supports-tools, owner, and encrypted API key columns. New endpoint columns need matching startup migration helpers.

`McpServer` includes stdio/SSE/HTTP transport config, plaintext env JSON, OAuth config, disabled tool names, and encrypted generic OAuth token/client state in `oauth_tokens`.

`CalendarCal.account_id` links synced local calendars back to one saved CalDAV account so multi-account sync/writeback can round-trip remote calendar identity.

`EncryptedText` owns transparent encrypted-at-rest DB columns via `src.secret_storage` for model endpoint keys and signatures. Email passwords are `String` columns encrypted/decrypted manually. Integrations, CalDAV/CardDAV prefs, and other JSON stores can use `src.secret_storage` directly. API tokens are bcrypt-hashed, API-key manager state uses `data/.key` plus `data/api_keys.json`, and vault state in `data/vault.json` is chmod-restricted JSON. Legacy plaintext rows are tolerated until migration or rewrite.

Current JSON/local stores include:

- `data/auth.json` for users, password hashes, TOTP, privileges, and auth settings;
- `data/sessions.json` for persisted browser session tokens;
- `data/settings.json`, user preferences, feature flags, integration settings, and `data/embedding_endpoint.json`;
- presets, API key manager state, memory/skills state, upload metadata, personal docs indexes, research JSON, background jobs, contacts/vault JSON, and task/cookbook auxiliary state.

`core.atomic_io` owns atomic file-write behavior for auth/settings/integration-style stores. Upload metadata uses its own locked atomic writer with `.bak` recovery. Memory and user prefs use temp-and-rename. API keys preserve encrypted values when saving one provider, while presets, research, and some older stores still use simpler or direct JSON writes with load-time fallback behavior.

Persisted memories, skills, documents, email, RAG chunks, notes, and other user-editable data are untrusted when reintroduced to model context. Route and processor code must pass them through the untrusted-context contract described in `context-building.md` and `auth-security.md`.

## Backup And Restore

`routes/backup_routes.py` owns narrow admin HTTP JSON export/import for memories, presets, skills, settings, features, and prefs. Skill import writes through the disk-backed skills manager API. This is not a full system restore path.

`scripts/odysseus-backup` owns local `data/` snapshot/restore, with some large/runtime subtrees such as deep research and mail attachments behind flags. It uses SQLite backup APIs, includes secret-bearing key files and stores, and validates restore archives against path escapes and link entries. Backup artifacts should be treated as sensitive.

## Transitional Notes

The repo still mixes database-backed and JSON-backed persistence. Some domains have both legacy manager state and newer SQLAlchemy rows. `src.database` remains a live compatibility import path. `services/memory/memory.py` and `services/memory/memory_vector.py` now re-export canonical `src` memory classes; preserve compatibility unless the change explicitly migrates a store and includes backfill/tests.

Docker bind-mounts `data/`, `logs/`, cache/local state, and optional Chroma state. The entrypoint repairs ownership for `PUID`/`PGID` before dropping privileges. POSIX secret files attempt restrictive chmod; Windows permission hardening is best-effort/no-op through platform compatibility helpers.

ChromaDB/vector stores are optional durable storage outside `data/app.db`; missing Chroma degrades RAG, memory-vector, and tool-index features without blocking core SQLite/JSON persistence. Vector collections can be lane-suffixed for custom HTTP embeddings versus FastEmbed fallback. See `documents-rag-uploads.md`.

## Current Gaps

- Migration behavior is centralized but long and manual.
- Ownerless legacy rows make access-control reasoning harder.
- Some JSON store shapes are only documented by manager code and tests.
- Startup migrations lack a legacy-schema/idempotence test harness for owner backfills, encrypted-secret rewrites, and repeated runs.
- JSON-store atomicity is inconsistent across stores, though prefs and upload metadata now have focused atomic-write paths.
- Agent filesystem tools currently allow broad `data/` access; secret-bearing files under `data/` need explicit deny coverage.
