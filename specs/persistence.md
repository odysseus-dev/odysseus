# Persistence

Last updated: dev@df2fad2 | 2026-07-12

## Scope

This spec covers durable state in:

- `core/database.py`;
- `src/database.py`;
- `src/runtime_paths.py`;
- `src/constants.py`;
- `core/models.py`;
- `core/session_manager.py`;
- `core/atomic_io.py`;
- `src/attachment_refs.py`, `src/upload_handler.py`, and
  `routes/upload_routes.py` for durable upload references and retention;
- JSON stores managed by `core/auth.py`, `src/settings.py`, `src/api_key_manager.py`, `src/preset_manager.py`, `src/integrations.py`, `src/upload_handler.py`, `src/personal_docs.py`, `src/research_handler.py`, `src/bg_jobs.py`, `routes/prefs_routes.py`, canonical `routes/contacts/contacts_routes.py` plus its shim, `routes/vault_routes.py`, `routes/cookbook_routes.py`, and memory/skills managers;
- `routes/email_helpers.py` scheduled-email storage;
- `routes/backup_routes.py` and `scripts/odysseus-backup`;
- runtime data under `data/`.

## Database Shape

`core/database.py` owns SQLAlchemy models and startup migrations. `src/database.py` is a compatibility re-export for legacy imports. Route and service code commonly owns its own `SessionLocal()` lifecycle instead of using one central unit-of-work wrapper.

The default database is SQLite at `DATA_DIR/app.db`. `src.runtime_paths` and `src.constants` own the data-dir default: source runs use the repository `data/` directory, frozen builds default to `~/.odysseus/data`, and `ODYSSEUS_DATA_DIR` overrides both. SQLAlchemy can point at a non-SQLite `DATABASE_URL`, but current startup migrations/backfills are SQLite-first and often use `sqlite3`, `PRAGMA`, or SQLite catalog queries. External DBs are not fully migration-compatible unless those helpers are made backend-neutral.

After `Base.metadata.create_all()`, `init_db()` resolves file-backed SQLite
paths from SQLAlchemy's parsed engine URL and attempts to restrict the main
database plus existing `-journal`, `-wal`, and `-shm` sidecars to `0600` on
POSIX. Driver-qualified, query-tagged, and local `file:` URI forms are covered;
non-SQLite, in-memory SQLite, and Windows paths are skipped. A failed POSIX
chmod is logged because the database and sidecars can contain password/token
hashes and encrypted provider material.

Timestamp defaults use `utcnow_naive()` so existing naive `DateTime` columns stay UTC without the deprecated `datetime.utcnow()` default.

Current model families include:

- chat sessions, messages, and `chat_messages_fts` transcript-search state/triggers;
- documents and document versions;
- gallery albums/images, editor drafts, signatures, generated-media metadata;
- email accounts, model endpoints, MCP servers, comparisons;
- provider auth sessions for OAuth/device-flow-backed provider credentials;
- API tokens, admin-global webhooks, user tools/tool data, integrations;
- crew members, scheduled tasks, task runs, notes;
- memory rows, calendar calendars, and calendar events.

Chat persistence stores model-readable text plus compact attachment-reference
lines in `chat_messages.content`, while structured references remain in message
metadata. Provider data URLs used by the live turn are not duplicated into the
durable transcript. The FTS migration recreates insert/update triggers to omit
inline media and scrubs legacy indexed rows that still contain data URLs.

Current calendar/task persistence includes CalDAV remote identity columns (`CalendarCal.remote_href`, `CalendarCal.remote_etag`, `CalendarEvent.remote_href`, `CalendarEvent.remote_etag`), `CalendarEvent.caldav_sync_pending` for retryable writeback state, and `ScheduledTask.character_id` for built-in task persona selection.

`EmailAccount` includes encrypted password fields plus Google OAuth fields (`oauth_provider`, encrypted access/refresh tokens, token expiry) and optional `display_name`. Startup migrations add those OAuth/display columns idempotently for older databases.

`core/models.py` owns pure dataclasses used by `SessionManager`. It does not own database persistence.

`routes/email_helpers.py` owns a second SQLite database at `data/scheduled_emails.db` for scheduled email, summary, reply, tag, sender-signature, urgency-alert, calendar-extraction, and cache state. Its migrations and owner backfills are local to that module, not `core/database.py`, and those auxiliary tables are owner-scoped.

## Migration Policy

Odysseus does not use Alembic. `core.database.init_db()` runs at module import, before FastAPI lifespan startup. `Base.metadata.create_all()` creates missing tables; hand-written `_migrate_*` functions add or reshape legacy columns.

Runtime behavior:

- migrations must be idempotent;
- SQLite foreign keys are enabled for every engine connection;
- new SQLAlchemy columns need matching startup migration code;
- legacy ownerless/shared rows may exist and must be handled by owner-aware route helpers.

Startup backfills include document-owner backfill from linked sessions, blanket legacy owner assignment for SQL and selected JSON stores, `user_prefs.json` per-user nesting, email account seeding from legacy settings, and encryption rewrites for legacy plaintext endpoint, signature, and email secrets. Failed encryption rewrites are logged and retried on later startup.

Owner-claiming is partly automatic and partly manual. `core.database._migrate_assign_legacy_owner()` assigns many ownerless SQL rows and selected JSON records to the primary admin when auth data exists, while `scripts/claim_ownerless.py` is an explicit local utility for claiming older ownerless memories, skills, sessions, documents, gallery rows, and comparisons.

## Ownership And Access

Owner columns are security-relevant. Current owner-bearing domains include sessions, documents, gallery images/albums, editor drafts, model endpoints, signatures, API tokens, user tools/tool data, comparisons, crew members, scheduled tasks/task runs, memories, notes, calendars/events, email accounts, and integrations. Webhooks are admin-global today and do not have an owner column.

Route code owns filtering for its domain. `src.auth_helpers.owner_filter()` is the common helper where available; gallery, documents, calendar, email, skills, and other surfaces also use local filters. Null-owner compatibility is domain-specific: shared endpoints may include null owners, while strict gates and disk stores may reject them. Do not rely on frontend filtering for access control.

There is no single anonymous/local owner value today. SQL `NULL` and missing JSON owners usually mean legacy/shared/unscoped compatibility; route-level no-login helpers use the empty string `""` when `AUTH_ENABLED=false`; chat/agent paths can pass `owner=None`; and calendar routes normalize empty owners to `ODYSSEUS_FALLBACK_OWNER` or `owner@localhost`. Email account helpers treat ownerless rows as single-user/global only for empty-owner mode; for non-empty owners, old ownerless account rows are visible only when the mailbox/from-address matches the owner. Lower-level helpers such as `get_upcoming_events(owner=None)` treat `None` as no owner scoping, so multi-user callers must pass a non-empty owner deliberately.

## Secrets And Local Stores

`ModelEndpoint` includes cached/hidden/pinned model lists, endpoint kind, refresh mode/interval/timeout, model type, supports-tools, owner, optional `provider_auth_id`, provider metadata, and encrypted API key columns. New endpoint columns need matching startup migration helpers.

`ProviderAuthSession` rows hold OAuth/device-flow credential state for providers such as ChatGPT Subscription. Endpoints can reference those rows through `provider_auth_id`; deletion/cleanup must preserve auth rows still referenced by another endpoint and remove orphaned provider-auth rows only after the last endpoint reference is gone.

`McpServer` includes stdio/SSE/HTTP transport config, plaintext env JSON, OAuth config, disabled tool names, and encrypted generic OAuth token/client state in `oauth_tokens`. Generic MCP token storage treats valid non-object JSON as empty state on reads and replaces it with an object on the next write instead of crashing callers.

`CalendarCal.account_id` links synced local calendars back to one saved CalDAV account so multi-account sync/writeback can round-trip remote calendar identity. Remote href/etag columns on calendars and events preserve CalDAV server identity across pull/push cycles, while `caldav_sync_pending` marks local create/update/delete work that still needs remote writeback.

`EncryptedText` owns transparent encrypted-at-rest DB columns via `src.secret_storage` for model endpoint keys and signatures. Email passwords and Google OAuth access/refresh tokens are `String` columns encrypted/decrypted manually. Integrations, CalDAV/CardDAV prefs, and other JSON stores can use `src.secret_storage` directly. API tokens are bcrypt-hashed, API-key manager state uses `data/.key` plus `data/api_keys.json` with restrictive chmod where supported, and vault state in `data/vault.json` is chmod-restricted JSON. Legacy plaintext rows are tolerated until migration or rewrite.

Current JSON/local stores include:

- `data/auth.json` for users, password hashes, TOTP, privileges, and auth settings;
- `data/sessions.json` for persisted browser session tokens;
- `data/settings.json`, user preferences, feature flags, integration settings, and `data/embedding_endpoint.json`;
- presets, API key manager state, memory/skills state, upload metadata, personal docs indexes, research JSON, background jobs, contacts/vault JSON, and task/cookbook auxiliary state.

Cookbook state lives under the shared `DATA_DIR` path through the `COOKBOOK_STATE_FILE` constant. Search cache/analytics, FastEmbed cache fallback, uploads, generated media, logs, and auxiliary SQLite stores also resolve from shared data-dir constants and must work with source, Docker, and frozen data-dir defaults.

`core.atomic_io` owns atomic file-write behavior for auth/settings/integration-style stores. Upload metadata uses its own locked atomic writer with `.bak` recovery and can rewrite owner fields plus owner-qualified index keys during user rename. Attachment-bearing chat/session, document, note, and calendar writers take owner-checked upload reservations before their durable writes; reservations share the upload-index lock with cleanup and refresh access time. Cleanup receives a complete reference snapshot from chat content/metadata, document current/version content, gallery rows, notes, and calendar rows, and removes only expired uploads proven unreferenced with coherent index state. Missing/incomplete scans fail closed, and index rows are restored when byte deletion fails. Memory and user prefs use temp-and-rename. API keys preserve encrypted values when saving one provider, presets persist atomically, and settings/feature loads degrade to defaults when the store is unreadable.

Persisted memories, skills, documents, email, RAG chunks, notes, and other user-editable data are untrusted when reintroduced to model context. Route and processor code must pass them through the untrusted-context contract described in `context-building.md` and `auth-security.md`.

## Backup And Restore

`routes/backup_routes.py` owns narrow admin HTTP JSON export/import for memories, presets, skills, settings, features, and prefs. Skill import writes through the disk-backed skills manager API. This is not a full system restore path.

`scripts/odysseus-backup` owns local `data/` snapshot/restore, with some large/runtime subtrees such as deep research and mail attachments behind flags. It uses SQLite backup APIs, includes secret-bearing key files and stores, validates restore archives against path escapes and link entries, and skips list entries that disappear or become unstatable during directory iteration. Backup artifacts should be treated as sensitive.

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
