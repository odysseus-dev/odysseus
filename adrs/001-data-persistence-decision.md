# ADR: Data Persistence — Per-Domain Architecture Decisions

- Status: proposed
- Deciders: Felix, active maintainers
- Date: 2026-06-12 (original), 2026-06-15 (domain analysis added)

## Context

Odysseus currently uses multiple persistence styles without explicit architectural decisions about which backend serves which data domain:

1. SQLite databases: `data/app.db` (primary, via SQLAlchemy), `data/scheduled_emails.db` (email-specific), and `data/email_cache.db` (dead/orphan — see use-case 12)
2. JSON/state files: 13+ distinct files under `data/` managed through `core/atomic_io` or domain-specific writers
3. File-system directories: uploads, generated images, skills, background jobs, caches
4. Optional vector store: ChromaDB for RAG, memory, and tool indexing
5. Browser storage: localStorage/sessionStorage for UI state (out of scope for this ADR)

This dual-store architecture creates concrete problems documented in [persistence.md](https://github.com/RaresKeY/odysseus/blob/docs/specs-bootstrap/specs/persistence.md):

- No single source of truth across stores. Route and service code owns its own `SessionLocal()` lifecycle instead of using one central unit-of-work wrapper.
- No cross-store transactions. JSON writes and SQLite writes can succeed independently, leaving inconsistent state.
- Referential integrity between JSON and SQL stores must be enforced in application logic.
- Operational complexity: migrations, backups, and owner-scoping require different approaches per store.
- Four distinct owner states (`NULL`, `""`, `None`, `owner@localhost`) depending on subsystem, complicating access control.

This ADR does not propose a blanket migration. Following the approach outlined by RaresKeY in PR #4101 discussion, it maps each persistence domain, documents why the current or a recommended backend is appropriate, and only proposes changes where there is a concrete, reviewable reason.

### A note on "human-readable JSON" as justification

Several JSON stores in Odysseus have no stronger reason for being JSON than historical convention or the assumption that human-readability matters. In practice:

- SQLite supports JSON columns and `json()` functions for semi-structured data. A `SELECT json(value) FROM config` is no harder to inspect than `cat settings.json`.
- SQLite provides crash safety via WAL journaling for free. JSON stores use a patchwork of write strategies: `core.atomic_io.atomic_write_json` (temp+fsync+replace) for most stores, a custom locked writer with `.bak` recovery for uploads, hand-rolled temp+replace for memory and user prefs, and plain `json.dump`/`write_text` with no crash safety at all for api_keys and vault.
- Nobody shares Odysseus config files between instances. The "easy to copy a JSON file" argument does not apply.
- Every JSON store that needs ownership, transactions, or querying must reinvent those features in application code. SQLite provides them natively.

This ADR recommends SQLite as the default for application state stores. The only JSON stores that remain as "Needs discussion" are `auth.json` and `sessions.json`, and that is due to migration risk (security-critical data), not because JSON is the right backend for them.

Reference: [persistence.md spec](https://github.com/RaresKeY/odysseus/blob/docs/specs-bootstrap/specs/persistence.md) and the full [specs set from PR #2538](https://github.com/pewdiepie-archdaemon/odysseus/pull/2538) provided initial context. Each use-case was then verified against the application code (`core/database.py`, `src/constants.py`, route files, and storage call sites) to catch domains, dual-store issues, and dead code that specs alone did not surface.

## Per-Domain Recommendation Categories

- **Keep current** — the existing backend fits the access pattern; no migration needed.
- **Add SQLite reference** — files stay on disk, but a SQLite table tracks metadata, ownership, and lifecycle. Example: `data/uploads/` files remain on disk, but an `uploads` table in `app.db` tracks metadata and owner.
- **Migrate to SQLite** — the domain would benefit from moving its durable state into `app.db` for transactions, ownership, queryability, or operational simplicity.
- **Needs discussion** — the tradeoffs are not clear-cut from access patterns alone; maintainer input needed.
- **Remove** — dead or orphan store that should be deleted.

---

## Domain I. Core Application State

These domains are already in SQLite `app.db` and are well-served by it.

### Use-case 1. Chat Sessions and Messages

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Session`, `ChatMessage`, `chat_messages_fts` |
| Access pattern | High read/write, complex queries (search, history, FTS), concurrent streaming |
| Ownership model | Owner-scoped; session ownership verified before loading |
| Atomicity | DB transactions via `SessionLocal()` |
| Backup coverage | Included in `scripts/odysseus-backup` SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Chat is the highest-traffic domain with complex query needs (full-text search, history pagination, session listing). SQLite with FTS is the right fit. The `SessionManager` pattern works well here.

### Use-case 2. Documents and Document Versions

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Document`, `DocumentVersion` |
| Access pattern | Moderate read/write, version history queries, owner-filtered listing |
| Ownership model | Owner-scoped; document access should be owner-filtered, not session-id-only |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Documents need version history, owner filtering, and relational links to sessions. SQLite handles this well. The `session_id` relinking gap noted in specs is a code-level fix, not a storage-level problem.

### Use-case 3. Comparisons

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Comparison` |
| Access pattern | Low-moderate write (votes), read for history; blind_mapping stored as JSON column |
| Ownership model | Owner-scoped; legacy `NULL` owner rows not treated as shared |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Comparison metadata is relational (links to sessions, models, votes). JSON column for `blind_mapping` is appropriate for semi-structured per-row data within SQLite.

---

## Domain II. Authentication and Security

### Use-case 4. User Accounts and Auth Config

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/auth.json` (lock-guarded) |
| Access pattern | Low write (user CRUD, login), moderate read (every auth check hits cache); password hashes, TOTP, privileges |
| Ownership model | Global admin store |
| Atomicity | Lock-guarded writes via `core/auth.py` |
| Backup coverage | Included in `scripts/odysseus-backup`; secret-bearing |
| Notes | Cross-store rename (JSON auth + SQLite owner rows + disk skills) is a data integrity risk if any step fails partially. |

**Recommendation:** Needs discussion

**Rationale:** `auth.json` holds security-critical data (password hashes, TOTP secrets, privileges). The lock-guarded JSON pattern works for low-write scenarios, but SQLite would provide better crash safety (WAL journaling vs. atomic file rewrite) and simpler querying for multi-user deployments. Migration risk is high due to the security sensitivity — any migration must preserve bcrypt hashes, TOTP state, and privilege mappings exactly. The low write frequency and working lock-guard pattern make this a judgment call rather than an obvious win.

### Use-case 5. Session Tokens

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/sessions.json` (lock-guarded) |
| Access pattern | Moderate write (login/logout/expiry), read on every request (cached in-process) |
| Ownership model | Per-user tokens |
| Atomicity | Lock-guarded writes |
| Backup coverage | Included in backup; secret-bearing |
| Notes | |

**Recommendation:** Needs discussion

**Rationale:** Session tokens are tightly coupled to auth.json. If auth moves to SQLite, sessions should follow. If auth stays JSON, sessions should stay JSON. These two stores should share the same backend.

### Use-case 6. API Tokens

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ApiToken` (bcrypt hashed) |
| Access pattern | Low write (CRUD), read on every bearer-token request (prefix cache in-process) |
| Ownership model | Owner-scoped with admin visibility |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** API tokens are relational (owner, scopes, timestamps, active flag). SQLite is the right home. The bcrypt hash + prefix cache pattern is sound.

### Use-case 7. API Key Manager State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/api_keys.json` + `data/.key` |
| Access pattern | Low write (provider key updates), low read; encrypted values preserved across saves |
| Ownership model | Global |
| Atomicity | Plain `json.dump` — no atomic write, no crash safety. Preserves encrypted values when saving one provider. |
| Backup coverage | Included in backup; secret-bearing |
| Notes | `data/.key` is a symmetric encryption key — if lost, all encrypted provider keys become unrecoverable. Saving one provider preserves other providers' encrypted values (partial-write safety). |

**Recommendation:** Needs discussion

**Rationale:** This store manages per-provider API keys with encryption. It is small and low-traffic. SQLite could consolidate this with `ModelEndpoint` encrypted key storage, but the current pattern works. Worth discussing whether this duplicates `ModelEndpoint.api_key` functionality.

### Use-case 8. Encryption Keys

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/.app_key` (Fernet key, chmod 0600) and `data/.key` (API key manager key) |
| Access pattern | Write-once on first startup, read on every encrypt/decrypt operation |
| Ownership model | Global — single key for entire instance |
| Atomicity | Atomic write via `atomic_write_text` |
| Backup coverage | Included in `scripts/odysseus-backup`; **the most critical secret files in the system** |
| Notes | If `data/.app_key` is lost, ALL `EncryptedText` columns become unrecoverable: model endpoint API keys, provider auth tokens, email passwords, signatures. If `data/.key` is lost, all API key manager state is unrecoverable. |

**Recommendation:** Keep current

**Rationale:** Encryption key files must remain as files. They are the root of the encryption chain — storing them inside the database they encrypt would be circular (the key needed to read `app.db`'s encrypted columns cannot itself be an encrypted column in `app.db`). The chmod 0600 permission is appropriate. These files should be prominently documented as the single most important backup targets in the system.

---

## Domain III. Communication

### Use-case 9. Email Accounts

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `EmailAccount` (encrypted passwords via `src.secret_storage`) |
| Access pattern | Low write (account CRUD), moderate read (account discovery for send/receive) |
| Ownership model | Owner-scoped; empty owner treated as single-user compatibility |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | Can still match legacy ownerless account rows by IMAP username — cross-owner data leak risk in multi-user. |

**Recommendation:** Keep current

**Rationale:** Email accounts are relational with encrypted credentials. SQLite handles this well.

### Use-case 10. Scheduled Email State

| Attribute | Value |
|-----------|-------|
| Current backend | Separate SQLite — `data/scheduled_emails.db` |
| Access pattern | Moderate write (schedule/send/cache), moderate read; owner-scoped |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions (separate DB) |
| Backup coverage | Included in backup (separate file) |
| Notes | Thread-boundary rows keyed by message shape rather than owner/account/mailbox — cross-owner data leak point. |

**Recommendation:** Migrate to SQLite (consolidate into `app.db`)

**Rationale:** This is the only domain using a separate SQLite database. Module isolation does not justify a separate database file — code-level module boundaries (keeping helper tables and migrations in `routes/email_helpers.py`) do not require a separate database. Consolidating into `app.db` eliminates a second backup target, enables cross-store queries (e.g., joining scheduled emails with `EmailAccount` rows), unifies the migration path, and removes a separate connection pool. The `core/database.py` surface area increase is manageable — the models move there but the route-level helper code stays in `email_helpers.py`.

### Use-case 11. Contacts

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/contacts.json` (when CardDAV unconfigured) / CardDAV remote |
| Access pattern | Low-moderate read/write; admin-only; import/export support |
| Ownership model | Global admin-only |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Contacts are a fallback store for when CardDAV is not configured. The primary source of truth for contacts is the remote CardDAV server. The JSON fallback is simple, low-traffic, and admin-only. Adding SQLite complexity for a fallback store is not justified.

### Use-case 12. Email Cache

| Attribute | Value |
|-----------|-------|
| Current backend | Separate SQLite — `data/email_cache.db` (defined in `EMAIL_CACHE_DB` constant) |
| Access pattern | Read-only by MCP email server (`_get_cached_summaries`); **no writer exists in the codebase** |
| Ownership model | None — the table has no owner column |
| Atomicity | N/A — never written to |
| Backup coverage | Not critical |
| Notes | **Dead/orphan store.** MCP email server reads table `email_ai` (columns: `subject, sender, summary, suggested_reply`) from `email_cache.db`. No code creates this table or writes to it. The main app's email cache uses `scheduled_emails.db` with table `email_ai_replies` (different DB, different table name, different schema). This is either legacy code from a prior cache implementation or a split-brain artifact that was never wired up. |

**Recommendation:** Remove

**Rationale:** This is dead code. The `EMAIL_CACHE_DB` constant, the `_get_cached_summaries()` function, and the `email_ai` table reference in `mcp_servers/email_server.py` should be removed. The actual email AI cache lives in `scheduled_emails.db` as `email_ai_replies` (covered by use-case 10). Keeping a dead database reference creates confusion about the email caching architecture.

### Use-case 13. Email Attachment Staging

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/mail-attachments/` (`ODYSSEUS_MAIL_ATTACHMENTS_DIR`) |
| Access pattern | Write on compose upload, read on send; per-folder/UID subdirectories |
| Ownership model | Implicit via email account ownership |
| Atomicity | File-level writes |
| Backup coverage | Behind flags in `scripts/odysseus-backup` (large subtree) |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Email attachments are binary files staged for SMTP send. Files on disk is the correct storage — no queries, no ownership tracking beyond the email flow. Missing staged files are skipped with warnings during send.

---

## Domain IV. Calendar, Tasks, and Notes

All domains in this group are already in SQLite `app.db`.

### Use-case 14. Calendars and Events

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `CalendarCal`, `CalendarEvent` |
| Access pattern | Moderate read/write; CalDAV sync with local SQLite as source of truth |
| Ownership model | Owner-scoped; empty owner normalized to `ODYSSEUS_FALLBACK_OWNER` or `owner@localhost` |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import; ICS import/export is separate |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Calendar events are relational with complex query needs (recurrence expansion, sync state, account linkage). SQLite is the right fit. The backup gap (calendar not in HTTP export/import) is a separate issue.

### Use-case 15. Scheduled Tasks and Task Runs

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ScheduledTask`, `TaskRun` |
| Access pattern | Moderate read/write; scheduler queries for next-run, status transitions |
| Ownership model | Owner-scoped; chained tasks validated as same-owner |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Tasks need relational state (run history, chaining, event-bus triggers). SQLite handles this well.

### Use-case 16. Notes and Todos

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Note` |
| Access pattern | Moderate read/write; due dates, ordering, repeat state |
| Ownership model | Owner-scoped with null-owner compatibility for legacy data |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Notes are relational with due dates, ordering, and reminder linkage. SQLite is appropriate.

### Use-case 17. Crew Members

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `CrewMember` |
| Access pattern | Low read/write; assistant configuration |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Small relational domain. SQLite is fine.

---

## Domain V. Media and Files

### Use-case 18. Gallery Images and Albums

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `GalleryImage`, `GalleryAlbum` + file system `data/generated_images/` |
| Access pattern | Moderate write (upload/generate), moderate read (library, serving); files served directly |
| Ownership model | Owner-scoped in DB; null-owner compatibility for generated files; MCP can create ownerless rows |
| Atomicity | DB transactions for metadata; file writes are separate |
| Backup coverage | Images included in backup; DB rows in SQLite backup |
| Notes | MCP image generation can create ownerless rows — owner attribution gap. |

**Recommendation:** Keep current

**Rationale:** The split is correct — binary image files on disk, metadata/ownership/albums in SQLite. This is a standard pattern for media storage.

### Use-case 19. Editor Drafts and Signatures

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `EditorDraft`, `Signature` |
| Access pattern | Low-moderate write; draft auto-save, signature CRUD |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Small relational domains with owner filtering. SQLite is appropriate.

### Use-case 20. Upload Files and Metadata

| Attribute | Value |
|-----------|-------|
| Current backend | File system `data/uploads/` + JSON `uploads.json` (atomic writes with `.bak` recovery) |
| Access pattern | Moderate write (uploads), moderate read (resolve by ID); owner-qualified index keys |
| Ownership model | Owner-scoped via `UploadHandler.resolve_upload()`; owner rename rewrites index keys |
| Atomicity | Locked atomic writer with `.bak` recovery for metadata |
| Backup coverage | Upload files included in backup; metadata JSON included |
| Notes | |

**Recommendation:** Add SQLite reference

**Rationale:** Upload files should stay on disk (binary data, direct serving). However, the `uploads.json` metadata store would benefit from SQLite: it needs owner filtering, ID lookups, and currently uses a custom locked atomic writer with `.bak` recovery. Moving metadata to an `uploads` table in `app.db` would give transactional consistency with other owner-scoped domains, eliminate the custom atomic writer, and enable SQL queries for cleanup/stats. The file-system storage remains unchanged.

### Use-case 21. Emoji Cache

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/emoji_cache/{codepoint}.svg` |
| Access pattern | Write-once per codepoint, frequent read for serving |
| Ownership model | Global cache |
| Atomicity | None needed — write-once cache |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

**Recommendation:** Keep current

**Rationale:** This is a simple file cache. SVG files are fetched once from OpenMoji and served repeatedly. No ownership, no queries, no transactions needed. Files are the right storage for a content cache.

### Use-case 22. TTS Audio Cache

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/tts_cache/{provider}_{model}_{voice}_{speed}_{hash}` |
| Access pattern | Write-once per unique synthesis, frequent read; no TTL or owner partition |
| Ownership model | Global — no owner partition (privacy gap noted in specs) |
| Atomicity | None needed — write-once cache |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

**Recommendation:** Keep current

**Rationale:** TTS cache is a content-addressable file cache. The privacy gap (no owner partition) is a policy decision, not a storage-backend issue — it could be addressed by adding owner prefixes to filenames without changing the storage model.

---

## Domain VI. Memory, Skills, and Knowledge

### Use-case 23. Persistent Memories

| Attribute | Value |
|-----------|-------|
| Current backend | **Dual-store**: JSON `data/memory.json` (primary, via `MemoryManager`) AND SQLite `app.db` `memories` table (via `Memory` SQLAlchemy model) |
| Access pattern | Moderate write (extraction adds), moderate read (retrieval per chat); owner fields, pinned state, use counts |
| Ownership model | Owner-scoped in both stores; vector dedup checks owner before suppression |
| Atomicity | JSON: temp-and-rename (full-file rewrite). SQLite: DB transactions |
| Backup coverage | JSON included in HTTP export/import and `scripts/odysseus-backup`; SQLite rows included in DB backup |
| Notes | **Active dual-store**: `MemoryManager` reads/writes `memory.json`, but `builtin_actions.py` queries the `Memory` SQLAlchemy model directly via `db.query(Memory).filter(Memory.owner == owner)`. Both stores are live — data consistency between them is unclear. Full-file JSON rewrite on every add/edit/delete. Import does not rebuild vector indexes. |

**Recommendation:** Migrate to SQLite (complete the partial migration)

**Rationale:** The `Memory` SQLAlchemy model already exists in `core/database.py` and is actively queried by builtin actions. The codebase is in a dual-store state where `memory.json` is the primary write path but SQLite rows are read directly by other subsystems. This is the exact "no single source of truth" problem this ADR aims to solve. The migration path is partially built — the model exists, the table exists. What remains: make `MemoryManager` read/write SQLite instead of JSON, backfill existing `memory.json` data, and retire the JSON store. The vector store (ChromaDB) remains separate.

### Use-case 24. Skills

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/skills/{category}/{name}/SKILL.md` + `_usage.json` sidecars |
| Access pattern | Low write (extraction/import), moderate read (matching per chat); directory tree with frontmatter |
| Ownership model | Owner in frontmatter; owner rename updates frontmatter and usage keys |
| Atomicity | File-level writes |
| Backup coverage | Included in HTTP export/import and `scripts/odysseus-backup` |
| Notes | Agent skill index and MCP memory access are not owner-scoped — cross-owner data leak in multi-user. |

**Recommendation:** Add SQLite reference

**Rationale:** Skills as markdown files with frontmatter is a reasonable authoring format — users can edit `SKILL.md` directly. However, the metadata (owner, category, tags, usage counts) would benefit from a SQLite index table for efficient owner-filtered queries and search. The `SKILL.md` files stay on disk as the content source of truth; a `skills` table in `app.db` indexes the metadata. This mirrors the gallery pattern (files on disk, metadata in SQLite).

### Use-case 25. Vector Embeddings (ChromaDB)

| Attribute | Value |
|-----------|-------|
| Current backend | ChromaDB (optional external service) with lane-specific collections |
| Access pattern | Write on document/memory indexing, read on RAG/memory retrieval |
| Ownership model | Owner-scoped chunk IDs; lane separation for HTTP vs FastEmbed embeddings |
| Atomicity | ChromaDB-managed |
| Backup coverage | Not included in standard backup; optional Chroma state in Docker volumes |
| Notes | **Live bug ([#1967](https://github.com/pewdiepie-archdaemon/odysseus/issues/1967), fix in [#1968](https://github.com/pewdiepie-archdaemon/odysseus/pull/1968)):** Admin wipe route does `from src.memory_vector import get_memory_vector_store`, but that function does not exist — the only accessor is the `MemoryVectorStore` class constructed in `app_initializer`. The import throws, the `try/except` swallows it, and "wipe memory" silently leaves every embedding behind. Semantic search returns ghost results after a full wipe. |

**Recommendation:** Keep current

**Rationale:** Vector embeddings belong in a purpose-built vector store. ChromaDB is the right tool for similarity search. The optional/degraded behavior (keyword fallback when Chroma is unavailable) is appropriate for an optional dependency.

### Use-case 26. Research Reports

| Attribute | Value |
|-----------|-------|
| Current backend | JSON files — `data/deep_research/{session_id}.json` |
| Access pattern | Write once per research job, moderate read (library, report rendering); large JSON payloads with sources, findings, stats |
| Ownership model | Owner stamped in JSON; cross-owner access should return 404 |
| Atomicity | File-level writes |
| Backup coverage | Partially included in backup (behind flags for large subtrees) |
| Notes | Agent tools and CLI access research JSON directly without owner-filter gates — cross-owner data access bypass. |

**Recommendation:** Add SQLite reference

**Rationale:** Research report files can be large (full reports with sources and findings). The JSON files should stay on disk. However, a SQLite reference table would enable owner-filtered library queries, archive/delete state management, and category/date filtering without reading every JSON file. Currently, listing the research library requires scanning the directory and reading each file's metadata.

### Use-case 27. Personal Document Indexes

| Attribute | Value |
|-----------|-------|
| Current backend | File-backed via `PersonalDocsManager` |
| Access pattern | Low write (re-index), read on RAG retrieval |
| Ownership model | Admin-gated directory indexing; RAG retrieval owner-filtered |
| Atomicity | Manager-level |
| Backup coverage | Not explicitly backed up (regenerable from source documents) |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Personal doc indexes are regenerable from source documents and primarily feed into the vector store. No separate persistence decision needed.

---

## Domain VII. Settings, Configuration, and Integrations

### Use-case 28. Global Settings

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/settings.json` (cached, defaults fallback) |
| Access pattern | Low write (admin changes), high read (every request can check settings); merged over defaults |
| Ownership model | Global |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in HTTP export/import and `scripts/odysseus-backup`; secret-bearing |
| Notes | Settings reference `ModelEndpoint` IDs — no foreign-key enforcement between JSON and SQLite (referential integrity gap). |

**Recommendation:** Migrate to SQLite

**Rationale:** Settings contain secrets (API keys, provider credentials) and are already cached in-process, so the "easy to read the JSON file" argument does not reflect actual usage — the app reads from cache, not disk. A `settings` table (or a single-row `config` table with a JSON column) would provide crash-safe writes via WAL journaling instead of relying on file-level atomicity, eliminate the custom defaults-fallback-on-corrupt-file code, and bring secret-bearing state under the same backup/restore path as other SQLite domains. The merge-over-defaults pattern works identically with `COALESCE` or application-level merge from a JSON column. Settings and feature flags could share a single `config` table. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 29. Feature Flags

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/features.json` (cached, defaults fallback) |
| Access pattern | Very low write (admin toggles), high read (feature checks); simple boolean map |
| Ownership model | Global |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in HTTP export/import |
| Notes | |

**Recommendation:** Migrate to SQLite

**Rationale:** Feature flags are a simple boolean map with the same defaults-fallback and corrupt-file recovery code as settings. They should migrate alongside settings — a `config` table with a `domain` column (e.g., `settings`, `features`) or a JSON column per domain eliminates a separate store, a separate fallback path, and a separate backup concern. There is no technical reason for this to be a standalone JSON file. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 30. User Preferences

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/user_prefs.json` (`_users` multi-user storage, legacy flat prefs support) |
| Access pattern | Low write (user changes), moderate read (overlaid on settings per request) |
| Ownership model | Per-user within `_users` key; whitelist of per-user overridable settings |
| Atomicity | Own temp+fsync+replace (not shared `atomic_write_json`) |
| Backup coverage | Included in HTTP export/import |
| Notes | Any user's pref change rewrites all users' prefs (full-file rewrite). |

**Recommendation:** Migrate to SQLite

**Rationale:** User preferences are per-user state embedded in a single JSON file via a `_users` key. The entire file is rewritten on any user's preference change — a write to user A's prefs rewrites user B's prefs too. This is the same full-file-rewrite scaling problem as `memory.json`. A `user_prefs` table with `(owner, key, value)` rows gives per-user atomic writes, eliminates the legacy flat-prefs compatibility layer, and aligns with the owner-scoped pattern used by every other per-user domain in SQLite.

### Use-case 31. Presets

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/presets.json` (atomic writes, corrupt-store fallback) |
| Access pattern | Low write (admin mutations), moderate read (preset expansion); shared store |
| Ownership model | Shared/global — not owner-scoped |
| Atomicity | Atomic writes via shared `core.atomic_io.atomic_write_json` |
| Backup coverage | Included in HTTP export/import |
| Notes | |

**Recommendation:** Migrate to SQLite

**Rationale:** `PresetManager` uses the shared `atomic_write_json` (not a custom writer), but implements its own corrupt-store fallback in `load()` — that fallback code would be eliminated by SQLite. `McpServer` is an equally low-traffic global admin store and nobody questioned putting it in SQLite. Presets contain structured data (templates, groups) that would benefit from per-row queries rather than full-file reads. The specs also note an unresolved decision about whether `user_templates` and `group_presets` should be owner-scoped — SQLite would make adding owner columns trivial if that decision goes that way. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 32. Model Endpoints

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ModelEndpoint` |
| Access pattern | Low write (CRUD), moderate read (model picker, resolution); encrypted API keys |
| Ownership model | Nullable owner (NULL = legacy/shared, non-null = private); admins see all |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | Decrypted endpoint headers can be copied into session metadata — endpoint deletion must clear dependent settings and copied session headers or stale secrets persist. |

**Recommendation:** Keep current

**Rationale:** Model endpoints are relational with encrypted secrets, owner filtering, and multiple columns (kind, refresh policy, cached models, supports-tools). SQLite is the right fit.

### Use-case 33. MCP Server Configs

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `McpServer` |
| Access pattern | Low write (admin CRUD), low read; transport config, encrypted OAuth state |
| Ownership model | Global (not owner-scoped) |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | `McpServer.env` stores environment variables as plaintext JSON (not encrypted) — potential secrets unencrypted in DB. |

**Recommendation:** Keep current

**Rationale:** MCP servers store transport config, encrypted OAuth tokens, and disabled tool lists. SQLite handles the encrypted columns and structured data well.

### Use-case 34. Integration Presets

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/integrations.json` |
| Access pattern | Very low write/read; generic API integration templates |
| Ownership model | Global |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | |

**Recommendation:** Migrate to SQLite

**Rationale:** There is already a dormant `Integration` SQLAlchemy model in `core/database.py` — the codebase is halfway to SQLite for this domain. The current JSON store and the dead model create confusion about which is authoritative. Migrating to the existing (or a revised) `Integration` model resolves this ambiguity, eliminates a standalone JSON file, and brings integration state under the standard SQLite backup path. The store is tiny, so migration effort is minimal. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 35. Vault Config

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/vault.json` (chmod 0600) |
| Access pattern | Very low write (config/login/logout), low read; stores `BW_SESSION` |
| Ownership model | Admin-only |
| Atomicity | Plain `write_text()` — no atomic write, no crash safety |
| Backup coverage | Included in backup; secret-bearing |
| Notes | No crash safety: a crash during `write_text()` can corrupt `vault.json`. |

**Recommendation:** Migrate to SQLite

**Rationale:** The chmod 0600 argument does not hold up. `app.db` already contains encrypted API keys, bcrypt password hashes, TOTP secrets, and bearer token hashes — it should be chmod 0600 itself. If it is not, that is a security bug to fix, not a reason to keep vault secrets in a separate file. Moving `BW_SESSION` and vault config into `app.db` (using the `EncryptedText` pattern for the session token) consolidates secret-bearing state into one file with one permission boundary. The vault-specific chmod code can be removed, and the vault route's corrupt-or-non-object config fallback becomes a standard SQLite query. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 36. Embedding Endpoint Config

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/embedding_endpoint.json` |
| Access pattern | Very low write, low read; small config |
| Ownership model | Global |
| Atomicity | Plain `write_text()` — no atomic write, no crash safety |
| Backup coverage | Included in backup |
| Notes | |

**Recommendation:** Migrate to SQLite

**Rationale:** A standalone JSON file for a single configuration value is hard to justify when settings and model endpoints are already in SQLite. This config could be a row in the `config` table alongside settings and feature flags, or a column on a relevant model endpoint row. Eliminating the file removes one more store from the backup/migration surface. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 37. Provider Auth Sessions

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ProviderAuthSession` |
| Access pattern | Low write (OAuth/device-flow grants), low read (token refresh, provider calls) |
| Ownership model | Linked to `ModelEndpoint` via `provider_auth_id`; used by ChatGPT Subscription, GitHub Copilot, and custom OAuth providers |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Provider auth sessions are relational (linked to model endpoints) with OAuth-specific lifecycle (grant, refresh, revoke). SQLite is the right fit. These rows store credential state that enables provider access — they belong alongside `ModelEndpoint` in `app.db`.

### Use-case 38. Webhooks

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Webhook` |
| Access pattern | Low write (admin CRUD), low read (event dispatch); stores URL, secret, allowed events, delivery status/error |
| Ownership model | Admin-global — no owner column |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | Webhook secret is plaintext fallback (deprecation pending per specs). |

**Recommendation:** Keep current

**Rationale:** Webhooks are relational with event filtering, delivery state tracking, and secret management. SQLite is appropriate. The plaintext secret fallback is a code-level issue, not a storage-backend issue.

### Use-case 39. User Tools and Tool Data

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `UserTool`, `ToolData` |
| Access pattern | Low write (tool registration), low-moderate read (tool index, agent dispatch) |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** User tools are relational with owner filtering and tool-index integration. SQLite is the right fit.

---

## Domain VIII. Infrastructure and Runtime

### Use-case 40. Background Jobs

| Attribute | Value |
|-----------|-------|
| Current backend | JSON `data/bg_jobs.json` + file system `data/bg_jobs/*` (wrapper scripts, logs, exit codes) |
| Access pattern | Moderate write (job start/status/follow-up), moderate read (monitoring); capped result text |
| Ownership model | Session-scoped with owner context |
| Atomicity | `atomic_write_json` (shared) for state; separate files for logs/scripts |
| Backup coverage | Not critical — ephemeral runtime state |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Background jobs are inherently file-system-oriented — they produce wrapper scripts, log files, and exit codes. The JSON state file tracks job lifecycle. This pattern is appropriate for process-management state that interacts with the OS. Jobs are ephemeral and do not survive server restart in meaningful state.

### Use-case 41. Cookbook State File

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/cookbook_state.json` (shared with CLI) |
| Access pattern | Low write (serve start/stop), low read (status); server lists, env with encrypted HF tokens |
| Ownership model | Global — cookbook-created endpoints have null-owner shared rows |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | Stale browser state can overwrite server state (full-file-write race). |

**Recommendation:** Migrate to SQLite

**Rationale:** The "shared with CLI" argument is weak — `sqlite3` is available on every platform Odysseus supports, and the CLI already interacts with `app.db` for other operations (e.g., `scripts/odysseus-mcp` reads `McpServer` rows). Cookbook state contains encrypted HF tokens and the stale browser overwrite race is a real data integrity issue that row-level SQLite updates would eliminate. The specs also note an unresolved ownership decision for cookbook-created endpoints — SQLite makes adding owner columns straightforward. See [Open Question 1](#open-questions) for counter-arguments.

### Use-case 42. Cookbook Download Completeness

| Attribute | Value |
|-----------|-------|
| Current backend | Not persisted — derived at runtime by scanning the HF cache for `*.incomplete` blobs |
| Access pattern | Read on Serve tab render; `has_incomplete` computed live from HF cache scan, not from `cookbook_state.json` |
| Ownership model | Global |
| Atomicity | N/A — derived state, not written |
| Backup coverage | N/A |
| Notes | The Serve tab shows a model as "downloading" if incomplete blobs exist in the HF cache. This is two sources of truth disagreeing: the state file says "ready" but the cache scan says "still downloading." This is orthogonal to the storage backend for use-case 41. |

**Recommendation:** Needs discussion

**Rationale:** This is not a storage backend problem — it is a reconciliation problem between `cookbook_state.json` (or its SQLite successor) and a live HF cache directory scan. Moving the state file to SQLite (use-case 41) does not fix stalled-download status bugs because the "is this done?" answer comes from the filesystem, not the state store. The fix requires deciding which source is authoritative for download completeness and reconciling them, which is a separate design discussion from persistence backend choice.

### Use-case 43. Search Cache and Analytics

| Attribute | Value |
|-----------|-------|
| Current backend | File system — shared data dir; tolerates read-only layers during startup |
| Access pattern | Write on search, read for cache hits; ephemeral cache |
| Ownership model | Global cache |
| Atomicity | None needed — cache data |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Ephemeral search cache. Files are the right storage for cache data that should be discardable.

### Use-case 44. HuggingFace Model Cache

| Attribute | Value |
|-----------|-------|
| Current backend | External file system — `HF_HOME` |
| Access pattern | Write on download, read on model load; managed by HuggingFace libraries |
| Ownership model | Global — managed externally |
| Atomicity | HuggingFace-managed |
| Backup coverage | Excluded from backup (large, regenerable) |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Managed by upstream HuggingFace libraries. Odysseus should not own this storage pattern.

### Use-case 45. Reminder Dedupe State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/note_pings_<owner_slug>.json` (per-owner files) |
| Access pattern | Moderate write (scheduler ticks every 60s), moderate read (dedupe check before dispatch) |
| Ownership model | Per-owner files via owner slug in filename |
| Atomicity | File-level writes |
| Backup coverage | Not critical — ephemeral runtime state |
| Notes | |

**Recommendation:** Keep current

**Rationale:** Reminder dedupe is ephemeral runtime state — a cache of `{note_id: last_ping_timestamp}` that prevents duplicate notifications. Losing it causes at most one duplicate reminder. Per-owner files are appropriate for a cache that is pruned regularly by the scheduler.

### Use-case 46. Calendar Tidy State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/tidy_calendar_state.json` |
| Access pattern | Very low write (after tidy action), very low read (before tidy to check watermark) |
| Ownership model | Global |
| Atomicity | File-level writes |
| Backup coverage | Not critical — regenerable watermark |
| Notes | |

**Recommendation:** Keep current

**Rationale:** A simple watermark file tracking when the last calendar tidy/classify action ran. Losing it causes one redundant tidy pass. No ownership, no queries — a file is appropriate.

### Use-case 47. Memory Document

| Attribute | Value |
|-----------|-------|
| Current backend | File — `data/memory_doc.md` (atomic text write) |
| Access pattern | Low write, low read; markdown document summarizing user memory |
| Ownership model | Global |
| Atomicity | Atomic write via `atomic_write_text` |
| Backup coverage | Included in backup |
| Notes | |

**Recommendation:** Keep current

**Rationale:** A single markdown file generated from memory state. This is a derived/rendered artifact, not a primary data store. It should follow whatever backend memories use — if memories move to SQLite, this file could become a generated view rather than a separate store.

---

## Cross-Cutting Concerns

### Open Questions

1. **Debate on simple config vs appliction state:** For several config/state stores, this ADR recommends migration to SQLite but the case is weaker than for relational/owner-scoped domains. RaresKeY's feedback: "SQLite can also introduce unnecessary friction in areas where a database is not buying us much. Some local/config/cache/state data may be simpler and safer as files, especially when the access pattern is simple and the ownership/migration story is clear." Maintainers should weigh whether the migration benefits (crash safety, unified backup, eliminating per-store write code) justify the effort for these stores, or whether the simple access pattern means files are fine:

   | Domain | This ADR says | SQLite buys | Counter-argument |
   |--------|--------------|-------------|-----------------|
   | Settings (`settings.json`) | Migrate | Crash safety, secret-bearing state in one place | Simple config, read from cache, write rarely — works fine as-is |
   | Feature flags (`features.json`) | Migrate | Consolidate with settings | Boolean map — database adds friction for no real gain |
   | Presets (`presets.json`) | Migrate | Eliminates corrupt-store fallback in `load()` | Uses shared `atomic_write_json`, low-traffic, simple structure |
   | Integration presets (`integrations.json`) | Migrate | Resolves dormant model ambiguity | Tiny store — code cleanup, not a storage problem |
   | Embedding endpoint (`embedding_endpoint.json`) | Migrate | One fewer file | Single config value — trivially simple as a file |
   | Cookbook state (`cookbook_state.json`) | Migrate | Row-level updates, encrypted token pattern | Process management state, shared with CLI, low-traffic |
   | Vault config (`vault.json`) | Migrate | Consolidates secrets into `app.db` | Simple access pattern, security-sensitive, already works |

   The domains where SQLite is clearly solving a real problem (ownership, querying, scaling) are not in dispute: memories, user preferences, upload/skills/research metadata, and scheduled email consolidation.

   However, it is worth noting that almost none of these stores are "config files" in the traditional sense (deployed with the app, edited by an operator in a text editor before startup). They are **runtime application state created and modified through the Odysseus UI**:

   | Domain | Created/modified by | Looks like config? | Actually is |
   |--------|--------------------|--------------------|-------------|
   | Settings | Admin, via Settings modal in the UI | Yes — key-value pairs | Runtime application state. Values reference `ModelEndpoint` IDs, search provider names, and contain secrets that are peers to `ModelEndpoint.api_key`. |
   | Feature flags | Admin, via admin toggles in the UI | Yes — boolean map | Runtime application state. Gates application behavior at request time. |
   | Presets | Admin, via preset editor in the UI | Partially — templates | Runtime application state. Contains structured data (templates, groups) with potential future owner-scoping. |
   | Integration presets | Admin, via settings UI | Yes — API templates | Runtime application state. Stores base URLs, headers, auth patterns created through the UI. |
   | Embedding endpoint | Admin, via embedding admin UI | Yes — single URL | Runtime application state. Set through the UI, not a deployment config file. |
   | Cookbook state | App + admin, via Cookbook modal | No — server lists, tokens | Runtime process state with encrypted secrets. Modified by both UI actions and serve lifecycle. |
   | Vault config | Admin, via vault settings UI | Partially — server URL | Runtime application state. `BW_SESSION` is an ephemeral session token from a `bw login` action, not a deployment config. |

   RaresKeY's "local/config data may be simpler as files" framing assumes operator-managed config. In Odysseus, the admin UI is the primary interface — nobody runs `vim data/settings.json` as the normal workflow. If these stores are application state managed through the UI, they fit RaresKeY's own criterion of "durable application records" that belong in SQLite.

2. Ownership normalization: this ADR recommends a single canonical owner representation (see below). What should the canonical no-login / single-user owner value be? Should this be addressed here or in a dedicated ownership ADR?
3. Should `auth.json` and `sessions.json` migrate to SQLite for crash safety, or does the working lock-guard pattern justify keeping them as JSON? These are the highest-risk migration candidates due to security sensitivity.
4. Migration ordering: which domains should migrate first? A suggested priority based on impact vs. risk: (a) settings/features/embedding config → `config` table (low risk, eliminates 3 files), (b) presets + integration presets (low risk, eliminates corrupt-store fallback and dormant model ambiguity), (c) memories (medium risk, biggest user-facing improvement), (d) user preferences (low risk, eliminates full-file-rewrite scaling problem), (e) cookbook state (low-medium risk, CLI needs updating), (f) upload/skills/research metadata as SQLite references.

### Ownership Model — The Bigger Problem

Normalizing ownership is arguably worth more than the backend choice itself. Today the same concept — "who owns this data" — is expressed in four different shapes across the codebase:

| Shape | Where used | How it works |
|-------|-----------|--------------|
| SQL `owner` column + `owner_filter()` | Sessions, documents, gallery, calendar, tasks, notes, endpoints, API tokens, etc. | `NULL` treated as legacy/shared; `owner_filter()` includes null-owner rows for compatibility |
| `_users` blob in JSON | `user_prefs.json` | Per-user prefs nested under a `_users` key; entire file rewritten on any user's change |
| Frontmatter field | `data/skills/{cat}/{name}/SKILL.md` | Owner stored as YAML frontmatter string; rename requires file-level rewrite |
| Directory/filename encoding | `data/uploads/` (owner-qualified index keys), `data/note_pings_<owner_slug>.json` | Owner embedded in file paths or JSON keys; rename requires rewriting paths/keys |

On top of that, four distinct "no owner" values coexist:
- SQL `NULL` / JSON missing: legacy/shared/unscoped compatibility data
- `""` (empty string): `AUTH_ENABLED=false` route helpers
- `None` (Python): chat/agent paths when auth middleware is disabled
- `ODYSSEUS_FALLBACK_OWNER` / `owner@localhost`: calendar route normalization

This fragmentation means:
- **User rename is a cross-store data migration** that touches SQL rows, JSON files, disk files, and frontmatter. If any step fails partially, ownership splits across stores (see use-case 4 note).
- **Owner-scoping bugs are easy to introduce** because each store implements ownership differently. The use-case notes in this ADR flag six cross-owner data leak points (use-cases 9, 10, 18, 24, 26, and the agent skill index).
- **New features must learn four patterns** to implement ownership correctly.

Consolidating more domains into SQLite (as this ADR recommends) naturally reduces ownership shapes — SQL `owner` column + `owner_filter()` becomes the dominant pattern. But the "no owner" value fragmentation remains regardless of backend choice and needs its own decision.

**Recommendation:** Adopt a single canonical owner representation. For SQL domains, this means a consistent `owner` column convention (including a canonical value for the no-login case). For any remaining file-backed domains, ownership should be tracked via SQLite reference tables rather than encoded in filenames or frontmatter. User rename should be a single-transaction operation wherever possible — which requires ownership to live in one store, not four.

### Backup and Restore Coverage

Two backup mechanisms exist with different coverage:

| Mechanism | Covers | Misses |
|-----------|--------|--------|
| `routes/backup_routes.py` (HTTP) | Memories, presets, skills, settings, features, prefs | Calendar, tasks, notes, documents, gallery, sessions, email, MCP, endpoints |
| `scripts/odysseus-backup` (local) | SQLite backup of `app.db`, key files, JSON stores, skills tree | Some large subtrees behind flags (deep research, mail attachments) |

Consolidating more domains into `app.db` would increase the coverage of the local SQLite backup without needing domain-specific backup logic. If all the "Migrate to SQLite" recommendations in this ADR are implemented, the remaining file-backed stores are: `auth.json` and `sessions.json` (Needs discussion), `api_keys.json` (Needs discussion), `contacts.json` (Keep — CardDAV fallback), `bg_jobs.json` (Keep — process management), `note_pings_<owner>.json` (Keep — ephemeral dedupe), and `tidy_calendar_state.json` (Keep — watermark). That is roughly 6-7 JSON/state files, down from 13+. The stores that remain are either security-sensitive migration candidates, ephemeral caches, or fallback stores — not core application state. The `app.db` file should be chmod 0600 — it already contains encrypted API keys, bcrypt hashes, and bearer tokens.

### Separate SQLite Databases vs. Consolidated app.db

`scheduled_emails.db` is the only active domain using a separate SQLite database (`email_cache.db` also exists but is dead code — see use-case 12). Module isolation does not justify a separate database file — code-level module boundaries work fine within a shared database (see how `McpServer`, `EmailAccount`, `CalendarEvent`, etc. coexist in `app.db` while their route logic lives in separate files). This ADR recommends consolidating `scheduled_emails.db` into `app.db`.

### SQLite-as-Reference Pattern

Several file-backed domains would benefit from SQLite metadata tracking without moving file content into the database:

| Domain | Files stay on disk | SQLite tracks |
|--------|-------------------|---------------|
| Uploads | `data/uploads/*` | ID, owner, filename, hash, timestamps |
| Skills | `data/skills/**/*.md` | Owner, category, name, tags, usage counts |
| Research reports | `data/deep_research/*.json` | Owner, session_id, category, archived, timestamps |

This pattern provides:
- Owner-filtered queries without scanning files
- Transactional consistency between metadata and other owner-scoped domains
- Elimination of custom write code (uploads.json has its own locked writer with `.bak` recovery; memory.json has its own temp-and-replace)
- Standard backup via SQLite backup APIs

### Migration Risk Assessment

For domains where migration is recommended:

| Domain | Risk | Mitigation |
|--------|------|------------|
| Memories → SQLite | Medium — active store, owner fields, vector index coupling | Read-and-insert migration; preserve all fields; vector store stays separate; keep JSON reader as fallback during transition |
| Upload metadata → SQLite | Low — metadata only, files unchanged | Insert from `uploads.json`; keep atomic writer as read-only fallback |
| Skills metadata → SQLite | Low — metadata index, files remain authoritative | Build index from disk scan; files remain source of truth |
| Research index → SQLite | Low — metadata only, JSON files unchanged | Scan directory, insert metadata rows |
| Settings + Features → SQLite | Low — single-document stores, in-process cache unchanged | Read JSON on startup, write to `config` table; keep JSON reader as one-time migration fallback |
| User Preferences → SQLite | Low — per-user key-value pairs | Read `_users` map, insert rows per user; legacy flat-prefs support becomes a migration step, not permanent code |
| Presets → SQLite | Low — admin-managed | Read JSON, insert rows; `PresetManager` corrupt-store fallback in `load()` can be removed |
| Integration Presets → SQLite | Low — tiny store, dormant model already exists | Read JSON, populate `Integration` rows; remove dormant model ambiguity |
| Embedding Endpoint Config → SQLite | Low — single config value | Merge into `config` table or `ModelEndpoint` metadata |
| Cookbook State → SQLite | Low-Medium — shared with CLI, contains encrypted tokens | Read JSON, insert rows; CLI updated to read SQLite; encrypted tokens use `EncryptedText` pattern |
| Vault Config → SQLite | Low — tiny store, admin-only | Read JSON, insert row; `BW_SESSION` uses `EncryptedText`; remove vault-specific chmod code; ensure `app.db` is chmod 0600 |
| Scheduled Emails → `app.db` | Low — already SQLite, just moving tables | Move table definitions to `core/database.py`; startup migration copies rows from `scheduled_emails.db` if it exists; helper code in `email_helpers.py` switches to `app.db` session |
| Email Cache → Remove | None — dead code | Remove `EMAIL_CACHE_DB` constant, `_get_cached_summaries()` function, and `email_ai` table reference from `mcp_servers/email_server.py` |

## Prior Art

[Issue #728](https://github.com/pewdiepie-archdaemon/odysseus/issues/728) by CallumCarmicheal is a comprehensive earlier proposal covering the same territory. It independently identified every JSON-to-SQLite migration this ADR recommends, plus the `email_cache.db` dead code and the `memory.json` / `memories` table dual-store. It goes further into schema design (integer PKs, `owner_id` foreign keys, proposed SQLAlchemy models) which is out of scope for this ADR but valuable for whoever implements the migrations.
