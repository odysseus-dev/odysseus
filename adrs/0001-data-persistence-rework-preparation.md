# ADR-0001: Data Persistence — Domain Inventory and Preparation

- Status: proposed
- Deciders: Felix, active maintainers
- Date: 2026-06-16
- Parent tracker: [#4377](https://github.com/pewdiepie-archdaemon/odysseus/issues/4377)

## Summary

This ADR:

- Records the persistence domain inventory for Odysseus (47 use-cases across 8 domains).
- Decides on concrete preparation steps that are correct regardless of future migration direction:
  - Removing dead code.
  - Fixing `app.db` file permissions.
  - Auditing non-manager accessors.
  - Establishing a migration-risk ordering.
- Establishes the direction: consolidate durable application state into `app.db` where concrete benefits justify it. Does not decide on any specific domain migration or prioritization — those are individual future ADRs.

## Context

Odysseus currently uses multiple persistence styles without explicit architectural decisions about which backend serves which data domain:

1. SQLite databases: `data/app.db` (primary, via SQLAlchemy), `data/scheduled_emails.db` (email-specific), and `data/email_cache.db` (dead/orphan — see use-case 12)
2. JSON/state files: 13+ distinct files under `data/` managed through `core/atomic_io` or domain-specific writers
3. File-system directories: uploads, generated images, skills, background jobs, caches
4. Optional vector store: ChromaDB for RAG, memory, and tool indexing
5. Browser storage: localStorage/sessionStorage for UI state (out of scope for this document)

This dual-store architecture creates concrete problems documented in [persistence.md](https://github.com/RaresKeY/odysseus/blob/docs/specs-bootstrap/specs/persistence.md):

- No single source of truth across stores. Route and service code owns its own `SessionLocal()` lifecycle instead of using one central unit-of-work wrapper.
- No cross-store transactions. JSON writes and SQLite writes can succeed independently, leaving inconsistent state.
- Referential integrity between JSON and SQL stores must be enforced in application logic.
- Operational complexity: migrations, backups, and owner-scoping require different approaches per store.
- Four distinct owner states (`NULL`, `""`, `None`, `owner@localhost`) depending on subsystem, complicating access control.

Reference: [persistence.md spec](https://github.com/RaresKeY/odysseus/blob/docs/specs-bootstrap/specs/persistence.md) and the full [specs set from PR #2538](https://github.com/pewdiepie-archdaemon/odysseus/pull/2538) provided initial context. Each use-case was then verified against the application code (`core/database.py`, `src/constants.py`, route files, and storage call sites) to catch domains, dual-store issues, and dead code that specs alone did not surface.

## Decision

This ADR decides on four concrete preparation steps. These are correct regardless of which domains eventually migrate to SQLite and which stay as files.

### 1. Remove dead/orphan stores

Two stores have no active readers or writers and should be removed as dead code:

| Store | Issue | Evidence |
|-------|-------|----------|
| `data/email_cache.db` — MCP email server reads table `email_ai` but no code creates or writes to it. The actual email cache is `email_ai_replies` in `scheduled_emails.db` (different DB, different table, different schema). | [#4412](https://github.com/pewdiepie-archdaemon/odysseus/issues/4412) | See use-case 12 below. |
| `data/memory_doc.md` — `MEMORY_DOC` constant defined in `src/constants.py` and `src/config.py` but never imported or used by any other module. | [#4411](https://github.com/pewdiepie-archdaemon/odysseus/issues/4411) | See use-case 47 below. |

### 2. Fix `app.db` file permissions to 0600

`data/app.db` is currently world-readable (0644) while it already contains bearer-token hashes, encrypted provider keys, and TOTP secrets. `vault.json` and the encryption keys are 0600. This is an existing security gap — and a hard prerequisite before any additional secret-bearing state moves into `app.db`.

| Issue | PR |
|-------|-----|
| [#4407](https://github.com/pewdiepie-archdaemon/odysseus/issues/4407) | [#4420](https://github.com/pewdiepie-archdaemon/odysseus/pull/4420) |

### 3. Audit non-manager accessors before any migration

Multiple domains have code paths that bypass the designated manager and access the store directly. Any migration that updates only the manager will silently split the source of truth — and the split will not show up in tests that go through the manager. The accessor bypass table in the Cross-Cutting Concerns section below documents six verified bypass patterns.

| Issue |
|-------|
| [#4408](https://github.com/pewdiepie-archdaemon/odysseus/issues/4408) |

### 4. Establish migration-risk ordering

The Migration Risk Assessment table in the Cross-Cutting Concerns section below provides a recalibrated risk ordering for domains where migration has been discussed. Risks account for accessor bypass patterns, non-rebuildable indexes, multi-process writers, and shared scaffolding dependencies. This ordering should be used if and when specific migration ADRs are proposed.

### Related prerequisite issues

| Issue | Description | Depends on |
|-------|-------------|------------|
| [#4200](https://github.com/pewdiepie-archdaemon/odysseus/issues/4200) | Owner-identity contract (auth-disabled mode) | — |
| [#4410](https://github.com/pewdiepie-archdaemon/odysseus/issues/4410) | `rename_user` ownership fan-out → atomic | #4200 |
| [#4413](https://github.com/pewdiepie-archdaemon/odysseus/issues/4413) | Shared `config` table + secret-encryption scaffolding | #4407, #4408 |
| [#4409](https://github.com/pewdiepie-archdaemon/odysseus/issues/4409) | Consolidate `scheduled_emails.db` → `app.db` | — |
| [#1940](https://github.com/pewdiepie-archdaemon/odysseus/issues/1940) | `memory.json` lost-update race (dual-store) | — |
| [#1967](https://github.com/pewdiepie-archdaemon/odysseus/issues/1967) / [#1968](https://github.com/pewdiepie-archdaemon/odysseus/pull/1968) | Admin wipe leaves vector embeddings (live bug) | — |
| [#3517](https://github.com/pewdiepie-archdaemon/odysseus/issues/3517) | Hardcoded `vault.json` path | — |

## Consequences

- Dead stores are removed, reducing confusion about email caching architecture and unused constants.
- `app.db` file permissions are fixed, closing an existing security gap and unblocking future secret-bearing migrations.
- Non-manager accessor patterns are documented and audited before any migration, preventing silent source-of-truth splits.
- A risk-ordered migration sequence exists for when specific backend decisions are proposed, so implementation does not start with the hardest domains.
- Individual backend migration decisions (e.g., "move settings to SQLite") are explicitly deferred to future ADRs — this ADR only decides on preparation.

---

## Summary Index

This inventory covers 47 use-cases identified from specs and codebase analysis. It may not be exhaustive — additional persistence domains may exist in areas not covered by the current specs or in recently added features.

| UC | Use-case | Current backend |
|---|---|---|
| 1 | Chat Sessions and Messages | SQLite `app.db` |
| 2 | Documents and Document Versions | SQLite `app.db` |
| 3 | Comparisons | SQLite `app.db` |
| 4 | User Accounts and Auth Config | JSON `auth.json` |
| 5 | Session Tokens | JSON `sessions.json` |
| 6 | API Tokens | SQLite `app.db` |
| 7 | API Key Manager State | JSON `api_keys.json` + `.key` |
| 8 | Encryption Keys | filesystem `.app_key` / `.key` |
| 9 | Email Accounts | SQLite `app.db` |
| 10 | Scheduled Email State | separate SQLite `scheduled_emails.db` |
| 11 | Contacts | JSON `contacts.json` / CardDAV |
| 12 | Email Cache | separate SQLite `email_cache.db` (dead) |
| 13 | Email Attachment Staging | filesystem `mail-attachments/` |
| 14 | Calendars and Events | SQLite `app.db` |
| 15 | Scheduled Tasks and Task Runs | SQLite `app.db` |
| 16 | Notes and Todos | SQLite `app.db` |
| 17 | Crew Members | SQLite `app.db` |
| 18 | Gallery Images and Albums | SQLite + filesystem `generated_images/` |
| 19 | Editor Drafts and Signatures | SQLite `app.db` |
| 20 | Upload Files and Metadata | filesystem `uploads/` + JSON `uploads.json` |
| 21 | Emoji Cache | filesystem `emoji_cache/` |
| 22 | TTS Audio Cache | filesystem `tts_cache/` |
| 23 | Persistent Memories | dual-store `memory.json` + SQLite `memories` |
| 24 | Skills | filesystem `SKILL.md` + `_usage.json` |
| 25 | Vector Embeddings | ChromaDB |
| 26 | Research Reports | JSON `deep_research/{id}.json` |
| 27 | Personal Document Indexes | file-backed `PersonalDocsManager` |
| 28 | Global Settings | JSON `settings.json` |
| 29 | Feature Flags | JSON `features.json` |
| 30 | User Preferences | JSON `user_prefs.json` |
| 31 | Presets | JSON `presets.json` |
| 32 | Model Endpoints | SQLite `app.db` |
| 33 | MCP Server Configs | SQLite `app.db` |
| 34 | Integration Presets | JSON `integrations.json` |
| 35 | Vault Config | JSON `vault.json` |
| 36 | Embedding Endpoint Config | JSON `embedding_endpoint.json` |
| 37 | Provider Auth Sessions | SQLite `app.db` |
| 38 | Webhooks | SQLite `app.db` |
| 39 | User Tools and Tool Data | SQLite `app.db` |
| 40 | Background Jobs | JSON `bg_jobs.json` + filesystem |
| 41 | Cookbook State File | JSON `cookbook_state.json` |
| 42 | Cookbook Download Completeness | not persisted (derived at runtime) |
| 43 | Search Cache and Analytics | filesystem |
| 44 | HuggingFace Model Cache | external filesystem `HF_HOME` |
| 45 | Reminder Dedupe State | JSON `note_pings_<owner>.json` |
| 46 | Calendar Tidy State | JSON `tidy_calendar_state.json` |
| 47 | Memory Document | file `memory_doc.md` (dead) |

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

### Use-case 2. Documents and Document Versions

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Document`, `DocumentVersion` |
| Access pattern | Moderate read/write, version history queries, owner-filtered listing |
| Ownership model | Owner-scoped; document access should be owner-filtered, not session-id-only |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

### Use-case 3. Comparisons

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Comparison` |
| Access pattern | Low-moderate write (votes), read for history; blind_mapping stored as JSON column |
| Ownership model | Owner-scoped; legacy `NULL` owner rows not treated as shared |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

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

### Use-case 5. Session Tokens

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/sessions.json` (lock-guarded) |
| Access pattern | Moderate write (login/logout/expiry), read on every request (cached in-process) |
| Ownership model | Per-user tokens |
| Atomicity | Lock-guarded writes |
| Backup coverage | Included in backup; secret-bearing |
| Notes | |

### Use-case 6. API Tokens

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ApiToken` (bcrypt hashed) |
| Access pattern | Low write (CRUD), read on every bearer-token request (prefix cache in-process) |
| Ownership model | Owner-scoped with admin visibility |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

### Use-case 7. API Key Manager State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/api_keys.json` + `data/.key` |
| Access pattern | Low write (provider key updates), low read; encrypted values preserved across saves |
| Ownership model | Global |
| Atomicity | Plain `json.dump` — no atomic write, no crash safety. Preserves encrypted values when saving one provider. |
| Backup coverage | Included in backup; secret-bearing |
| Notes | `data/.key` is a symmetric encryption key — if lost, all encrypted provider keys become unrecoverable. Saving one provider preserves other providers' encrypted values (partial-write safety). |

### Use-case 8. Encryption Keys

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/.app_key` (Fernet key, chmod 0600) and `data/.key` (API key manager key) |
| Access pattern | Write-once on first startup, read on every encrypt/decrypt operation |
| Ownership model | Global — single key for entire instance |
| Atomicity | Atomic write via `atomic_write_text` |
| Backup coverage | Included in `scripts/odysseus-backup`; **the most critical secret files in the system** |
| Notes | If `data/.app_key` is lost, ALL `EncryptedText` columns become unrecoverable: model endpoint API keys, provider auth tokens, email passwords, signatures. If `data/.key` is lost, all API key manager state is unrecoverable. |

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

### Use-case 10. Scheduled Email State ([#4409](https://github.com/pewdiepie-archdaemon/odysseus/issues/4409))

| Attribute | Value |
|-----------|-------|
| Current backend | Separate SQLite — `data/scheduled_emails.db` |
| Access pattern | Moderate write (schedule/send/cache), moderate read; owner-scoped |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions (separate DB) |
| Backup coverage | Included in backup (separate file) |
| Notes | Thread-boundary rows keyed by message shape rather than owner/account/mailbox — cross-owner data leak point. |

### Use-case 11. Contacts

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/contacts.json` (when CardDAV unconfigured) / CardDAV remote |
| Access pattern | Low-moderate read/write; admin-only; import/export support |
| Ownership model | Global admin-only |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | |

### Use-case 12. Email Cache ([#4412](https://github.com/pewdiepie-archdaemon/odysseus/issues/4412))

| Attribute | Value |
|-----------|-------|
| Current backend | Separate SQLite — `data/email_cache.db` (defined in `EMAIL_CACHE_DB` constant) |
| Access pattern | Read-only by MCP email server (`_get_cached_summaries`); **no writer exists in the codebase** |
| Ownership model | None — the table has no owner column |
| Atomicity | N/A — never written to |
| Backup coverage | Not critical |
| Notes | **Dead/orphan store.** MCP email server reads table `email_ai` (columns: `subject, sender, summary, suggested_reply`) from `email_cache.db`. No code creates this table or writes to it. The main app's email cache uses `scheduled_emails.db` with table `email_ai_replies` (different DB, different table name, different schema). This is either legacy code from a prior cache implementation or a split-brain artifact that was never wired up. |

### Use-case 13. Email Attachment Staging

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/mail-attachments/` (`ODYSSEUS_MAIL_ATTACHMENTS_DIR`) |
| Access pattern | Write on compose upload, read on send; per-folder/UID subdirectories |
| Ownership model | Implicit via email account ownership |
| Atomicity | File-level writes |
| Backup coverage | Behind flags in `scripts/odysseus-backup` (large subtree) |
| Notes | |

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

### Use-case 15. Scheduled Tasks and Task Runs

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ScheduledTask`, `TaskRun` |
| Access pattern | Moderate read/write; scheduler queries for next-run, status transitions |
| Ownership model | Owner-scoped; chained tasks validated as same-owner |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

### Use-case 16. Notes and Todos

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Note` |
| Access pattern | Moderate read/write; due dates, ordering, repeat state |
| Ownership model | Owner-scoped with null-owner compatibility for legacy data |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

### Use-case 17. Crew Members

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `CrewMember` |
| Access pattern | Low read/write; assistant configuration |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Not included in HTTP backup/import |
| Notes | |

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

### Use-case 19. Editor Drafts and Signatures

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `EditorDraft`, `Signature` |
| Access pattern | Low-moderate write; draft auto-save, signature CRUD |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

### Use-case 20. Upload Files and Metadata

| Attribute | Value |
|-----------|-------|
| Current backend | File system `data/uploads/` + JSON `uploads.json` (atomic writes with `.bak` recovery) |
| Access pattern | Moderate write (uploads), moderate read (resolve by ID); owner-qualified index keys |
| Ownership model | Owner-scoped via `UploadHandler.resolve_upload()`; owner rename rewrites index keys |
| Atomicity | Locked atomic writer with `.bak` recovery for metadata |
| Backup coverage | Upload files included in backup; metadata JSON included |
| Notes | |

### Use-case 21. Emoji Cache

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/emoji_cache/{codepoint}.svg` |
| Access pattern | Write-once per codepoint, frequent read for serving |
| Ownership model | Global cache |
| Atomicity | None needed — write-once cache |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

### Use-case 22. TTS Audio Cache

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/tts_cache/{provider}_{model}_{voice}_{speed}_{hash}` |
| Access pattern | Write-once per unique synthesis, frequent read; no TTL or owner partition |
| Ownership model | Global — no owner partition (privacy gap noted in specs) |
| Atomicity | None needed — write-once cache |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

---

## Domain VI. Memory, Skills, and Knowledge

### Use-case 23. Persistent Memories ([#1940](https://github.com/pewdiepie-archdaemon/odysseus/issues/1940))

| Attribute | Value |
|-----------|-------|
| Current backend | **Dual-store**: JSON `data/memory.json` (primary, via `MemoryManager`) AND SQLite `app.db` `memories` table (via `Memory` SQLAlchemy model) |
| Access pattern | Moderate write (extraction adds), moderate read (retrieval per chat); owner fields, pinned state, use counts |
| Ownership model | Owner-scoped in both stores; vector dedup checks owner before suppression |
| Atomicity | JSON: temp-and-rename (full-file rewrite). SQLite: DB transactions |
| Backup coverage | JSON included in HTTP export/import and `scripts/odysseus-backup`; SQLite rows included in DB backup |
| Notes | **Active dual-store**: `MemoryManager` reads/writes `memory.json`, but `builtin_actions.py` queries the `Memory` SQLAlchemy model directly via `db.query(Memory).filter(Memory.owner == owner)`. Both stores are live — data consistency between them is unclear. Full-file JSON rewrite on every add/edit/delete. Import does not rebuild vector indexes. |

### Use-case 24. Skills

| Attribute | Value |
|-----------|-------|
| Current backend | File system — `data/skills/{category}/{name}/SKILL.md` + `_usage.json` sidecars |
| Access pattern | Low write (extraction/import), moderate read (matching per chat); directory tree with frontmatter |
| Ownership model | Owner in frontmatter; owner rename updates frontmatter and usage keys |
| Atomicity | File-level writes |
| Backup coverage | Included in HTTP export/import and `scripts/odysseus-backup` |
| Notes | Agent skill index and MCP memory access are not owner-scoped — cross-owner data leak in multi-user. |

### Use-case 25. Vector Embeddings (ChromaDB) ([#1967](https://github.com/pewdiepie-archdaemon/odysseus/issues/1967), [#1968](https://github.com/pewdiepie-archdaemon/odysseus/pull/1968))

| Attribute | Value |
|-----------|-------|
| Current backend | ChromaDB (optional external service) with lane-specific collections |
| Access pattern | Write on document/memory indexing, read on RAG/memory retrieval |
| Ownership model | Owner-scoped chunk IDs; lane separation for HTTP vs FastEmbed embeddings |
| Atomicity | ChromaDB-managed |
| Backup coverage | Not included in standard backup; optional Chroma state in Docker volumes |
| Notes | **Live bug ([#1967](https://github.com/pewdiepie-archdaemon/odysseus/issues/1967), fix in [#1968](https://github.com/pewdiepie-archdaemon/odysseus/pull/1968)):** Admin wipe route does `from src.memory_vector import get_memory_vector_store`, but that function does not exist — the only accessor is the `MemoryVectorStore` class constructed in `app_initializer`. The import throws, the `try/except` swallows it, and "wipe memory" silently leaves every embedding behind. Semantic search returns ghost results after a full wipe. |

### Use-case 26. Research Reports

| Attribute | Value |
|-----------|-------|
| Current backend | JSON files — `data/deep_research/{session_id}.json` |
| Access pattern | Write once per research job, moderate read (library, report rendering); large JSON payloads with sources, findings, stats |
| Ownership model | Owner stamped in JSON; cross-owner access should return 404 |
| Atomicity | File-level writes |
| Backup coverage | Partially included in backup (behind flags for large subtrees) |
| Notes | Agent tools and CLI access research JSON directly without owner-filter gates — cross-owner data access bypass. |

### Use-case 27. Personal Document Indexes

| Attribute | Value |
|-----------|-------|
| Current backend | File-backed via `PersonalDocsManager` |
| Access pattern | Low write (re-index), read on RAG retrieval |
| Ownership model | Admin-gated directory indexing; RAG retrieval owner-filtered |
| Atomicity | Manager-level |
| Backup coverage | Not explicitly backed up (regenerable from source documents) |
| Notes | |

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

### Use-case 29. Feature Flags

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/features.json` (cached, defaults fallback) |
| Access pattern | Very low write (admin toggles), high read (feature checks); simple boolean map |
| Ownership model | Global |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in HTTP export/import |
| Notes | |

### Use-case 30. User Preferences

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/user_prefs.json` (`_users` multi-user storage, legacy flat prefs support) |
| Access pattern | Low write (user changes), moderate read (overlaid on settings per request) |
| Ownership model | Per-user within `_users` key; whitelist of per-user overridable settings |
| Atomicity | Own temp+fsync+replace (not shared `atomic_write_json`) |
| Backup coverage | Included in HTTP export/import |
| Notes | Any user's pref change rewrites all users' prefs (full-file rewrite). |

### Use-case 31. Presets

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/presets.json` (atomic writes, corrupt-store fallback) |
| Access pattern | Low write (admin mutations), moderate read (preset expansion); shared store |
| Ownership model | Shared/global — not owner-scoped |
| Atomicity | Atomic writes via shared `core.atomic_io.atomic_write_json` |
| Backup coverage | Included in HTTP export/import |
| Notes | |

### Use-case 32. Model Endpoints

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ModelEndpoint` |
| Access pattern | Low write (CRUD), moderate read (model picker, resolution); encrypted API keys |
| Ownership model | Nullable owner (NULL = legacy/shared, non-null = private); admins see all |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | Decrypted endpoint headers can be copied into session metadata — endpoint deletion must clear dependent settings and copied session headers or stale secrets persist. |

### Use-case 33. MCP Server Configs

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `McpServer` |
| Access pattern | Low write (admin CRUD), low read; transport config, encrypted OAuth state |
| Ownership model | Global (not owner-scoped) |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | `McpServer.env` stores environment variables as plaintext JSON (not encrypted) — potential secrets unencrypted in DB. |

### Use-case 34. Integration Presets

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/integrations.json` |
| Access pattern | Very low write/read; generic API integration templates |
| Ownership model | Global |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | |

### Use-case 35. Vault Config ([#3517](https://github.com/pewdiepie-archdaemon/odysseus/issues/3517))

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/vault.json` (chmod 0600) |
| Access pattern | Very low write (config/login/logout), low read; stores `BW_SESSION` |
| Ownership model | Admin-only |
| Atomicity | Plain `write_text()` — no atomic write, no crash safety |
| Backup coverage | Included in backup; secret-bearing |
| Notes | No crash safety: a crash during `write_text()` can corrupt `vault.json`. |

### Use-case 36. Embedding Endpoint Config

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/embedding_endpoint.json` |
| Access pattern | Very low write, low read; small config |
| Ownership model | Global |
| Atomicity | Plain `write_text()` — no atomic write, no crash safety |
| Backup coverage | Included in backup |
| Notes | |

### Use-case 37. Provider Auth Sessions

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `ProviderAuthSession` |
| Access pattern | Low write (OAuth/device-flow grants), low read (token refresh, provider calls) |
| Ownership model | Linked to `ModelEndpoint` via `provider_auth_id`; used by ChatGPT Subscription, GitHub Copilot, and custom OAuth providers |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

### Use-case 38. Webhooks

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `Webhook` |
| Access pattern | Low write (admin CRUD), low read (event dispatch); stores URL, secret, allowed events, delivery status/error |
| Ownership model | Admin-global — no owner column |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | Webhook secret is plaintext fallback (deprecation pending per specs). |

### Use-case 39. User Tools and Tool Data

| Attribute | Value |
|-----------|-------|
| Current backend | SQLite `app.db` — `UserTool`, `ToolData` |
| Access pattern | Low write (tool registration), low-moderate read (tool index, agent dispatch) |
| Ownership model | Owner-scoped |
| Atomicity | DB transactions |
| Backup coverage | Included in SQLite backup |
| Notes | |

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

### Use-case 41. Cookbook State File

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/cookbook_state.json` (shared with CLI) |
| Access pattern | Low write (serve start/stop), low read (status); server lists, env with encrypted HF tokens |
| Ownership model | Global — cookbook-created endpoints have null-owner shared rows |
| Atomicity | `atomic_write_json` (shared) |
| Backup coverage | Included in backup |
| Notes | Stale browser state can overwrite server state (full-file-write race). |

### Use-case 42. Cookbook Download Completeness

| Attribute | Value |
|-----------|-------|
| Current backend | Not persisted — derived at runtime by scanning the HF cache for `*.incomplete` blobs |
| Access pattern | Read on Serve tab render; `has_incomplete` computed live from HF cache scan, not from `cookbook_state.json` |
| Ownership model | Global |
| Atomicity | N/A — derived state, not written |
| Backup coverage | N/A |
| Notes | The Serve tab shows a model as "downloading" if incomplete blobs exist in the HF cache. This is two sources of truth disagreeing: the state file says "ready" but the cache scan says "still downloading." This is orthogonal to the storage backend for use-case 41. |

### Use-case 43. Search Cache and Analytics

| Attribute | Value |
|-----------|-------|
| Current backend | File system — shared data dir; tolerates read-only layers during startup |
| Access pattern | Write on search, read for cache hits; ephemeral cache |
| Ownership model | Global cache |
| Atomicity | None needed — cache data |
| Backup coverage | Not critical — regenerable cache |
| Notes | |

### Use-case 44. HuggingFace Model Cache

| Attribute | Value |
|-----------|-------|
| Current backend | External file system — `HF_HOME` |
| Access pattern | Write on download, read on model load; managed by HuggingFace libraries |
| Ownership model | Global — managed externally |
| Atomicity | HuggingFace-managed |
| Backup coverage | Excluded from backup (large, regenerable) |
| Notes | |

### Use-case 45. Reminder Dedupe State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/note_pings_<owner_slug>.json` (per-owner files) |
| Access pattern | Moderate write (scheduler ticks every 60s), moderate read (dedupe check before dispatch) |
| Ownership model | Per-owner files via owner slug in filename |
| Atomicity | File-level writes |
| Backup coverage | Not critical — ephemeral runtime state |
| Notes | |

### Use-case 46. Calendar Tidy State

| Attribute | Value |
|-----------|-------|
| Current backend | JSON — `data/tidy_calendar_state.json` |
| Access pattern | Very low write (after tidy action), very low read (before tidy to check watermark) |
| Ownership model | Global |
| Atomicity | File-level writes |
| Backup coverage | Not critical — regenerable watermark |
| Notes | |

### Use-case 47. Memory Document ([#4411](https://github.com/pewdiepie-archdaemon/odysseus/issues/4411))

| Attribute | Value |
|-----------|-------|
| Current backend | File — `data/memory_doc.md` (defined in `src/constants.py`) |
| Access pattern | **No active read or write path found in codebase** |
| Ownership model | Global |
| Atomicity | N/A — never written to |
| Backup coverage | Included in backup if file exists |
| Notes | **Likely dead code.** `MEMORY_DOC` constant is defined in `src/constants.py` and `src/config.py` but is never imported or used by any other module. No code writes to or reads from this file. |

---

## Cross-Cutting Concerns

### Ownership Model — The Bigger Problem ([#4200](https://github.com/pewdiepie-archdaemon/odysseus/issues/4200), [#4410](https://github.com/pewdiepie-archdaemon/odysseus/issues/4410))

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
- **User rename is a cross-store fan-out with partial rollback.** One function (`rename_user` in `auth_routes.py`) rewrites ownership across SQL tables, `user_prefs.json`, research JSON files, `memory.json`, the uploads index, and skills frontmatter — each through a different mechanism. The auth and SQL steps have a rollback path. The four file-based steps do not: they log a warning on failure and continue. A rename that fails partway leaves ownership split across stores with no way back.
- **Owner-scoping bugs are easy to introduce** because each store implements ownership differently. The use-case notes in this document flag six cross-owner data leak points (use-cases 9, 10, 18, 24, 26, and the agent skill index).
- **New features must learn four patterns** to implement ownership correctly.

Consolidating ownership into a single `UPDATE ... SET owner = ?` is arguably worth more than any individual backend swap. Every domain that moves to SQLite reduces the rename fan-out by one store, and once ownership lives in one database, rename becomes a single transaction instead of a multi-store prayer.

### Backup and Restore Coverage

Two backup mechanisms exist with different coverage:

| Mechanism | Covers | Misses |
|-----------|--------|--------|
| `routes/backup_routes.py` (HTTP) | Memories, presets, skills, settings, features, prefs | Calendar, tasks, notes, documents, gallery, sessions, email, MCP, endpoints |
| `scripts/odysseus-backup` (local) | SQLite backup of `app.db`, key files, JSON stores, skills tree | Some large subtrees behind flags (deep research, mail attachments) |

The remaining file-backed stores are: `auth.json` and `sessions.json`, `api_keys.json`, `contacts.json` (CardDAV fallback), `bg_jobs.json` (process management), `note_pings_<owner>.json` (ephemeral dedupe), and `tidy_calendar_state.json` (watermark). The stores that remain as files are either security-sensitive, ephemeral caches, or fallback stores — not core application state.

### Separate SQLite Databases vs. Consolidated app.db

`scheduled_emails.db` is the only active domain using a separate SQLite database (`email_cache.db` also exists but is dead code — see use-case 12). Module isolation does not justify a separate database file — code-level module boundaries work fine within a shared database (see how `McpServer`, `EmailAccount`, `CalendarEvent`, etc. coexist in `app.db` while their route logic lives in separate files).

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

### Accessor Bypass — The Hidden Migration Risk ([#4408](https://github.com/pewdiepie-archdaemon/odysseus/issues/4408))

The real risk in every migration is not the manager itself — it is the code paths that bypass the manager and access the store directly. This pattern repeats across multiple domains. The per-domain analysis above was scoped against each manager's public API, but a migration that only updates the manager will silently split the source of truth. The split will not show up in tests that go through the manager.

Known bypass patterns, verified against code:

| Domain | Manager / expected path | Bypass code | What it does |
|--------|------------------------|-------------|-------------|
| Settings | `src.settings.save_settings()` | `contacts_routes._load_settings()` / `_save_settings()` | Reads/writes `settings.json` directly, no cache invalidation, no defaults-merge |
| Settings | `src.settings.save_settings()` | `email_helpers._load_settings()` / `_save_settings()` | Same: direct JSON read/write bypassing the settings module |
| Uploads | `UploadHandler` | `upload_routes.download_file()`, `upload_routes._load_upload_info()` | Reads `uploads.json` directly with `open()` + `json.load()`, outside `UploadHandler` |
| Skills | `SkillsManager` | Auth rename path in `auth_routes.py` | Rewrites `SKILL.md` frontmatter owner directly, does not call `SkillsManager` |
| Research | `ResearchHandler` | `research_routes.py` archive/delete | Writes research JSON with plain `write_text()` (not even atomic), bypassing handler |
| Memories | `MemoryManager` | `builtin_actions.py` | Reads `Memory` SQLAlchemy model directly via `db.query(Memory)`, bypassing `MemoryManager` entirely (the dual-store problem in use-case 23) |

Any migration PR must audit for these bypasses first. The migration itself is straightforward — change the manager to read/write SQLite. The dangerous part is the code that never goes through the manager. If those paths are not updated, the old file store and the new SQLite store will diverge, and the divergence will be silent.

### Migration Prerequisites ([#4407](https://github.com/pewdiepie-archdaemon/odysseus/issues/4407), [#4413](https://github.com/pewdiepie-archdaemon/odysseus/issues/4413))

Two hard prerequisites must be completed before any secret-bearing store migrates into `app.db`:

**1. `app.db` must be chmod 0600.**

Right now `data/app.db` is world-readable (0644), while `vault.json` and the encryption keys are 0600. The database already holds bearer-token hashes and encrypted provider keys — that is an existing security gap. Moving vault session tokens, integration secrets, settings API keys, cookbook HF tokens, or embedding API keys out of 0600 files into a 0644 database is a regression, not a consolidation. This is a hard precondition for stores containing secrets. The fix is straightforward (chmod in the entrypoint + startup), but it must land first.

**2. The config table scaffolding must be built once, not per-domain.**

Use-cases 28 (settings), 29 (features), and 36 (embedding config) all need a `config` table that does not exist yet, plus a migration path and a secret-encryption convention. Whoever lands the first of them pays the scaffolding cost and sets the convention the others inherit — they cannot each be independently low risk. Use-case 28 (settings) should be the anchor: it exercises every hard invariant (cache, defaults-merge, override-detection, secrets). Features and embedding config ride the same rails once settings is built.

### Migration Risk Assessment

Risk assessment for domains where migration has been discussed. Risks account for accessor bypass patterns (see above), non-rebuildable indexes, multi-process writers, and shared scaffolding dependencies. Audit for non-manager accessors before migrating any domain.

| Domain | Risk | Why | Mitigation |
|--------|------|-----|------------|
| Memories → SQLite | **Med-High** | No single write chokepoint: `memory.json` is written by `MemoryManager` and separately by the rename path; the SQLite `Memory` table has no writer at all. Memory IDs must be carried verbatim or the ChromaDB index orphans. | Audit all write paths before migration. Preserve IDs exactly. Keep JSON reader as fallback during transition. |
| Upload metadata → SQLite | **Medium** | The index is not rebuildable — `owner`, `hash`, and `original_name` live only in `uploads.json`, never on disk. Drift is permanent data loss. Two route handlers read `uploads.json` directly outside `UploadHandler`. | Audit bypass readers in `upload_routes.py`. Migration must be lossless — no fallback to "rebuild from disk." |
| Skills metadata → SQLite | Low-Medium | Rebuildable from disk scan so drift self-heals. But auth rename rewrites `SKILL.md` frontmatter directly, bypassing `SkillsManager`. | Build index from disk scan; files remain source of truth. Audit rename path. |
| Research index → SQLite | Low-Medium | Rebuildable from disk scan. But archive/delete in `research_routes.py` writes JSON with plain `write_text()` (not atomic), bypassing handler. | Scan directory, insert metadata rows. Audit route-level writers. |
| Settings + Features → SQLite | **Medium** | Hot read path, full-defaults-merge, `is_setting_overridden()` reads the raw file, in-band secrets, and writers that bypass `src/settings.py` (contacts_routes, email_helpers each have their own `_load_settings`/`_save_settings`). | Audit all bypass writers first. Migration must preserve defaults-merge semantics and cache invalidation. Config-table scaffolding needed before embedding config can merge in. |
| User Preferences → SQLite | Low-Medium | The auth-disabled "first user" overlay aliasing is a non-obvious invariant that a per-row schema can break. | Preserve first-user overlay semantics. Test auth-disabled single-user flow. |
| Presets → SQLite | Low | Needs the load-once in-memory `.presets` and defaults-forward-fill preserved. | Read JSON, insert rows. Preserve `PresetManager` defaults-healing and in-memory cache semantics. |
| Integration Presets → SQLite | Low | The "dormant model already exists" premise is misleading: the model is schema-incompatible with the JSON store and has no encrypted column for the API key. | Revise or replace the dormant `Integration` model rather than populating it as-is. |
| Embedding Endpoint Config → SQLite | Low | Depends on the config-table scaffolding from settings migration. | Merge into `config` table or `ModelEndpoint` metadata after settings migration lands. |
| Cookbook State → SQLite | **Med-High** | Several direct-disk accessors across three processes including a real CLI/cron writer. Race guards are application-level merge logic, not torn-write protection. | Audit all accessors across web app, CLI, and serve lifecycle. Row-level SQLite updates eliminate the file-overwrite race but application-level merge logic must be preserved. |
| Vault Config → SQLite | Low | Small, admin-only. Requires `app.db` chmod 0600 prerequisite. | Read JSON, insert row; `BW_SESSION` uses `EncryptedText`. |
| Scheduled Emails → `app.db` | **Med-High** | Nine tables, not one. Raw `sqlite3` connections to the separate DB are spread across five modules (`email_routes`, `email_pollers`, `email_helpers`, `task_routes`, `builtin_actions`). The "helpers stay in `email_helpers.py`" framing understates it: those helpers ARE the connection and migration machinery. | Audit all raw `sqlite3` connection sites. Move table definitions to `core/database.py` with SQLAlchemy models. Startup migration copies rows. Every `sqlite3.connect(SCHEDULED_DB)` call must switch to `SessionLocal()`. |
| Email Cache → Remove | None | Dead code. | Remove `EMAIL_CACHE_DB` constant, `_get_cached_summaries()`, and `email_ai` table reference from `mcp_servers/email_server.py`. |
| Memory Doc → Remove | None | Dead code. | Remove `MEMORY_DOC` constant from `src/constants.py` and `memory_doc` field from `src/config.py`. |

## Prior Work

- [#4377](https://github.com/pewdiepie-archdaemon/odysseus/issues/4377) — Storage architecture parent tracker (this ADR feeds into it).
- [#728](https://github.com/pewdiepie-archdaemon/odysseus/issues/728) — Earlier database architecture proposal (CallumCarmicheal) with detailed schema designs, independently identified the same domains.
- [#4101](https://github.com/pewdiepie-archdaemon/odysseus/pull/4101) / [#4100](https://github.com/pewdiepie-archdaemon/odysseus/issues/4100) — Original persistence ADR PR and discussion.
- [#2538](https://github.com/pewdiepie-archdaemon/odysseus/pull/2538) — Implementation-truth specs bootstrap.
