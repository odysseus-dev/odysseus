# Memory And Skills

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

This spec covers persistent memory and user skills in:

- app wiring in `app.py` and `src/app_initializer.py`;
- active legacy memory managers `src/memory.py` and `src/memory_vector.py`;
- canonical memory routes in `routes/memory/memory_routes.py`, with `routes/memory_routes.py` as a compatibility shim;
- chat memory/skill gating in `routes/chat_helpers.py`;
- memory compatibility modules in `services/memory/memory.py`, `services/memory/memory_vector.py`, and `services/memory/service.py`;
- provider abstractions in `src/memory_provider.py`;
- LLM extraction/audit in `services/memory/memory_extractor.py`;
- skill storage, format, import, and extraction in `services/memory/skills.py`, `services/memory/skill_format.py`, `services/memory/skill_importer.py`, and `services/memory/skill_extractor.py`;
- skill routes in `routes/skills_routes.py`;
- prompt/tool call sites in `src/chat_processor.py`, `src/agent_loop.py`, `src/ai_interaction.py`, `src/tool_implementations.py`, `src/tool_execution.py`, `src/tool_schemas.py`, and `src/tool_security.py`;
- MCP and Codex surfaces in `mcp_servers/memory_server.py` and `routes/codex_routes.py`;
- backup/admin/CLI surfaces in `routes/backup_routes.py`, canonical `routes/admin_wipe/admin_wipe_routes.py` plus its shim, `scripts/odysseus-memory`, `scripts/odysseus-skills`, and `scripts/odysseus-backup`;
- frontend modules `static/js/memory.js` and `static/js/skills.js`;
- tests under `tests/test_memory_*`, `tests/test_builtin_memory_consolidation.py`, `tests/test_skill_*`, and `tests/test_skills_*`.

## Memory Runtime

`src.app_initializer.initialize_managers()` creates the active `src.memory.MemoryManager` and `src.memory_vector.MemoryVectorStore` used by app startup. `routes.memory.memory_routes` imports through `services.memory` but is passed the startup manager instances; top-level `routes.memory_routes` is a `sys.modules` compatibility shim.

`MemoryManager` owns JSON-backed memory storage in `data/memory.json`, validation, owner fields, pinned state, use counts, and text/keyword similarity. Read-only `load_all()` remains lenient and can degrade an unreadable store to no memories. Mutating read-modify-write paths use `load_all_for_update()`, which raises `MemoryStoreUnreadable` rather than letting a corrupt or unreadable file be overwritten with an empty list. Agent/MCP/native-provider adds, extraction, backup import, and owner migration preserve that distinction; legacy `memory.txt` migration remains allowed. `MemoryVectorStore` owns semantic lookup when Chroma and embeddings are reachable.

Chat memory behavior:

- chat preferences and incognito state gate memory preface use;
- pinned memories are loaded for the owner;
- retrieved memories use keyword matching plus optional vector scoring;
- inserted memory is wrapped as untrusted context;
- memory use counts are incremented after insertion.

`services/memory/memory_extractor.py` owns LLM-assisted extraction, audit, and validation flows. It requests model behavior and writes through the memory manager; it does not own chat session persistence.

Extraction handles reasoning-model response shapes and records explicit dislike/drop preferences as `dislikes` rather than losing them to generic fact handling.

## Skills Runtime

`services/memory/skills.py` owns disk-backed skill storage under `data/skills/<category>/<name>/SKILL.md`, plus `_usage.json` usage/audit sidecars. Legacy `data/skills.json` is a read-only fallback/import source, not the current write shape.

`services/memory/skill_format.py` owns frontmatter/body parsing and emission. Quoted scalar parsing/emission is symmetric: JSON escapes decode once, UTF-8/non-ASCII stays intact, emitted values escape line separators safely, and invalid JSON-style escapes fall back to literal text instead of compounding backslashes on every save. `services/memory/skill_importer.py` resolves public GitHub/skills URLs, fetches bundle files with strict public-network URL safety, and chooses/imports `SKILL.md`. Import disables automatic redirects, follows at most five hops, validates and resolves each hop, then connects only to the validated IP snapshot through a pinned transport while preserving URL, Host, and TLS identity; GitHub final-host checks and file/size limits still apply. `routes/skills_routes.py` owns CRUD/search/index/import, owner filtering, skill test/audit jobs, and admin-gated built-in tool instruction overrides.

Skill extraction is owned by `services/memory/skill_extractor.py`. It can suggest or save skills from conversations, tries valid brace-delimited JSON candidates with `JSONDecoder.raw_decode()`, rejects ambiguous multiple top-level JSON objects instead of guessing, and saved skills remain user-editable data.

Agent skill behavior:

- matched skills are owner-scoped, confidence-gated, usage-counted, and wrapped as untrusted context;
- `index_for()` exposes published skills plus teacher-escalation drafts gated by platform and toolsets; `active_toolsets=None` means the caller has no explicit toolset knowledge and does not hide `requires_toolsets` skills, while an explicit list applies the gate;
- user prefs such as skills enabled, auto-approve, and max injected skills shape runtime insertion;
- the level-0 base skill index currently calls `index_for(owner=None)`, so it is not fully owner-scoped.
- skill tests use the configured utility model rather than the chat default and wrap user-editable skill text as untrusted context; approval continuation for a test or teacher-generated skill uses the same exact-action gate as the normal agent loop.

## Tools, MCP, And Backup

Native `manage_memory` and `manage_skills` tool paths pass owner context and use in-process policy gates. `manage_skills` requires an explicit action instead of silently defaulting a malformed call. Manual memory add can choose a category, and route-side manual add validates the source session owner before attaching session-derived memories. `mcp_servers/memory_server.py` lazy-initializes `src` managers and exposes list/add/edit/delete/search. It can scope to `ODYSSEUS_MCP_MEMORY_OWNER` or `ODYSSEUS_MEMORY_OWNER`; if the JSON store contains owner-bearing entries and no owner env is configured, it returns an owner-scope error instead of listing or mutating across owners. Ownerless stores remain ownerless compatibility mode.

The direct `odysseus-memory add` CLI tolerates non-object legacy/corrupt rows
when checking whether its newly added entry is already present; it ignores
those rows instead of calling mapping methods on them and crashing the add.

`/api/export` owner-filters memories and skills. `/api/import` imports skills through current disk-backed `SkillsManager` APIs, stamping missing owners to the importer and preserving supported skill metadata. Full data snapshots through `scripts/odysseus-backup` preserve on-disk skill trees, memory JSON, and caches differently from JSON import/export.

## Compatibility State

Memory and skills are partially migrated:

- app startup, MCP, and some tools still use `src.memory*`;
- services memory modules remain relevant for imports/tests, with memory and vector modules re-exporting canonical `src` implementations;
- `services/memory/service.py` is a compatibility facade around the canonical managers, but it remains ownerless and should not be assumed equivalent to route/tool owner policy;
- skills are service-owned and disk-backed, while backup import and some compatibility paths still expect older JSON/list shapes.

## Degraded Vector Memory

Chroma is an external HTTP service. Native defaults use `localhost:8100`; Docker uses `chromadb:8000`. Embeddings prefer configured HTTP endpoints and can fall back to local FastEmbed.

Startup can degrade to keyword-only memory when vector initialization fails. Extraction/audit paths catch vector failures and continue with text/JSON behavior. Vector dedup is checked against the current owner before suppressing a candidate, and audit rebuilds preserve other owners' vector rows. Chat retrieval assumes a healthy startup vector store remains usable, so post-start vector failures can still break memory retrieval unless handled by the caller.

Admin wipe currently has a vector cleanup compatibility gap because it imports a nonexistent helper before attempting vector clearing.

## Policy

Saved memories and skills are untrusted source data when shown to the model. A stored skill may contain useful instructions, but it is still user-editable content and must be framed consistently with prompt-injection policy.

Owner isolation is surface-specific:

- HTTP memory and skills routes are expected to owner-filter normal user data;
- native memory/skill tools are expected to pass owner context;
- Codex exposes scoped token memory behavior separately;
- normal memory/skills routes are cookie/current-user surfaces, not scoped token APIs;
- MCP memory uses an environment-configured owner for owner-scoped stores, while the agent level-0 skill index currently has ownerless/global behavior;
- vector dedup during memory extraction suppresses only same-owner or legacy-ownerless vector matches.

Skill test/audit flows intentionally run user-editable `SKILL.md` content as instructions inside controlled jobs. Those jobs rely on route owner checks, admin gates where applicable, and tool execution policy.

Skill import is admin-gated defense-in-depth, but imported URLs are still untrusted network input. Initial and redirected targets must remain public, automatic redirects stay disabled, and the connection must use only the IP set validated for that hop so DNS rebinding cannot change the destination between validation and transport.

User rename flows update skill frontmatter owner fields and `_usage.json` owner keys alongside memory/upload/research ownership migrations.

## Testing Coverage

Existing tests cover memory extraction/degraded vectors, owner isolation, unreadable-store mutation refusal, MCP memory shape/scope, skill owner update/delete, prompt-injection wrapping and approval continuation, utility-model selection, toolset gating, frontmatter escape round trips, skill-import redirect and DNS-rebinding/SSRF defenses, CLI non-object rows, and selected route owner checks.

Route-level memory CRUD/security, skills route security, MCP memory behavior, vector degraded writes, compatibility facade owner behavior, backup skill import, admin vector cleanup, and frontend endpoint wiring need broader coverage.

## Current Gaps

- `services/memory/service.py` needs an explicit owner-scope/support decision before it is treated as a public memory API.
- The agent level-0 skill index should thread owner or be documented as an intentional local/global index.
- MCP memory still needs a deliberate multi-user UX/config decision, but current behavior avoids cross-owner access when owner-bearing rows exist without an explicit MCP owner env.
- Memory JSON import does not rebuild vector indexes.
- Admin wipe vector clearing is currently ineffective.
- Chat memory retrieval needs a graceful path for vector failures after startup.
- Route-level memory and skills security coverage is incomplete.
