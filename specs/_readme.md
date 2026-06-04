# Specs DocumentMap

Last updated: dev@a3cb15d | 2026-06-06

This folder is the compact implementation-truth map for humans and coding agents working on Odysseus. Read this file first, then open only the subsystem specs that match the work.

Specs are living notes about current code shape and intended contracts. They are not product marketing, not PR planning, not templates, and not a replacement for source inspection or tests.

This `_readme.md` is the DocumentMap and control document. It is intentionally exempt from subsystem `Scope` and `Current Gaps` sections; keep it limited to the quality contract, working rules, subsystem map, and cross-cutting update triggers.

## Quality Contract

Each subsystem spec should stay compact and useful under context pressure:

- Start with `Last updated: dev@<short-sha> | YYYY-MM-DD`, using the
  upstream `dev` commit the spec text was inspected against.
- Use a concrete `Scope` section that names real files, route surfaces, frontend modules, data stores, and integration points.
- Use domain-specific sections. Do not force every spec into the same headings when the subsystem needs `Streaming`, `Tool Results`, `Optional Dependencies`, `Current Gaps`, or another focused section.
- State ownership clearly: which file owns a mapping, which layer only forwards state, and which caller requests behavior without owning implementation.
- Include runtime behavior bullets for flows that matter.
- Include "Current call sites include" when behavior is spread across many files.
- Record transitional compatibility notes, especially `src/` versus `services/` duplication.
- Record degraded, optional, or platform behavior where it changes runtime expectations.
- Record policy/provenance where relevant: untrusted context, encrypted secrets, API token scopes, optional dependency/license implications, generated media, or user data.
- End with `Current Gaps` only when there is a real known gap, not as filler.

If code and specs disagree, treat code as ground truth. Update specs only when
the current task explicitly includes spec maintenance or the PR intentionally
includes specs; otherwise report the drift in the relevant issue, PR review, or
project documentation.

## Working Rules

- Start here before substantial work.
- Read the related subsystem spec before changing code in that area. For cross-cutting work, include the owning domain spec plus route/runtime, auth/security, persistence, frontend, tool/context, integration, and testing/devops specs as applicable.
- Treat specs as read-only context during ordinary project work, PR review, and code review. Do not edit specs unless the user explicitly asks for spec work or the current PR intentionally includes spec changes.
- During explicit spec-maintenance work, update the related spec when source inspection shows behavior, ownership, security boundaries, data shape, import paths, or implementation contracts have changed.
- During ordinary work, record source/spec drift in the relevant issue, PR review, or project documentation instead of mutating specs.
- Keep specs dense but readable. Prefer current facts and invariants over broad explanation.
- Every non-index `specs/*.md` file should appear exactly once in the Subsystem Map with a one-line description and no dead link.
- Specs contain implementation truth. Planning, research, branch notes, and decisions belong in tracked project docs. Drafts, audit reports, raw exports, and exploratory gap lists are not authoritative until promoted into tracked docs or specs.
- Use repo source and these specs as the authority for Odysseus architecture. Do not treat global skill registries or external agent metadata as repo ground truth.

## Subsystem Map

- [runtime.md](runtime.md): FastAPI startup, router registration, static serving, lifespan, app-wide middleware.
- [auth-security.md](auth-security.md): auth, privileges, API tokens, security headers, untrusted data, SSRF and admin boundaries.
- [persistence.md](persistence.md): SQLite models, startup migrations, encrypted columns, ownership columns, data directory rules.
- [chat.md](chat.md): chat routes, sessions, streaming, uploads-in-chat, compare handoff, research/chat mode dispatch.
- [compare.md](compare.md): model A/B comparison runs, voting/history, compare frontend panes, compare ownership.
- [llm-models.md](llm-models.md): LLM provider calls, endpoint discovery, model context length, fallbacks, model endpoints.
- [agent-tools.md](agent-tools.md): agent loop, tool schemas, tool execution, tool retrieval, tool security, MCP tool exposure.
- [context-building.md](context-building.md): URL/search/RAG/memory/skills/YouTube/email/tool-output context, untrusted wrapping, unavailable context, intent boundaries.
- [search.md](search.md): web search providers, ranking, cache/analytics, URL fetch/content extraction, `src.search`/`services.search` split.
- [documents-rag-uploads.md](documents-rag-uploads.md): uploads, documents, PDF/form handling, personal docs, RAG/vector stores.
- [memory-skills.md](memory-skills.md): memory storage, semantic memory, skill extraction/formatting, owner isolation.
- [research.md](research.md): deep research jobs, synthesis, sources, research library, research UI panel.
- [calendar-tasks-notes.md](calendar-tasks-notes.md): CalDAV calendars, scheduled tasks, reminders, assistant runs, notes/todos.
- [email-contacts.md](email-contacts.md): IMAP/SMTP email, email library, scheduled mail, contacts/CardDAV.
- [gallery-editor-media.md](gallery-editor-media.md): gallery, generated media, image editor drafts, signatures, emoji/font helpers.
- [cookbook-hwfit.md](cookbook-hwfit.md): model downloads, local/remote model serving, hardware detection, fit ranking.
- [speech.md](speech.md): STT and TTS services, routes, settings, optional dependencies.
- [frontend.md](frontend.md): static SPA, module loading, UI conventions, major JS areas, no-build frontend shape.
- [integrations.md](integrations.md): Codex/Claude scoped APIs, companion pairing, webhooks, external agent access.
- [shell-mcp.md](shell-mcp.md): shell execution, background jobs, MCP manager, built-in MCP servers.
- [settings-admin.md](settings-admin.md): settings, preferences, presets, backup/import/export, diagnostics, admin wipe.
- [testing-devops.md](testing-devops.md): pytest, JS tests, Docker, scripts, requirements, local dev expectations.

## Cross-Cutting Spec Update Triggers

Use these triggers only during explicit spec-maintenance work or a PR that
intentionally includes specs. For ordinary work and code review, use the same
list to choose which specs to read and where to report drift.

- New route file or route prefix: update [runtime.md](runtime.md) and the owning subsystem spec.
- New SQLAlchemy model, column migration, durable JSON/local store, data directory, backup/import domain, or non-SQL persistence behavior: update [persistence.md](persistence.md) and the owning subsystem spec.
- New tool, tool schema, agent prompt rule, or tool security behavior: update [agent-tools.md](agent-tools.md) and [context-building.md](context-building.md) if it adds model context.
- New MCP runtime/config/built-in behavior: update [shell-mcp.md](shell-mcp.md), [agent-tools.md](agent-tools.md), and [context-building.md](context-building.md) when MCP tool results enter model context.
- New external content source, tool result, MCP/app API result, or integration result shown to an LLM: update [context-building.md](context-building.md) and [auth-security.md](auth-security.md).
- New API-token scope, scoped external API, webhook, companion/pairing route, generic integration provider, or external-agent helper bundle: update [integrations.md](integrations.md), [auth-security.md](auth-security.md), and the owning subsystem spec.
- New secret store, decrypted-secret return path, settings backup/import/export behavior, diagnostics/log output, vault/tool secret flow, `.env*` policy change, or credential-bearing CLI output: update [auth-security.md](auth-security.md), [settings-admin.md](settings-admin.md), [testing-devops.md](testing-devops.md), and the owning subsystem spec.
- New optional dependency, degraded fallback, platform/Docker/native/launcher difference, GPU overlay behavior, or retired compatibility shim: update [testing-devops.md](testing-devops.md) and the owning subsystem spec; also update [runtime.md](runtime.md), [llm-models.md](llm-models.md), [shell-mcp.md](shell-mcp.md), [cookbook-hwfit.md](cookbook-hwfit.md), or [persistence.md](persistence.md) when that layer owns the behavior.
- New frontend module or modal/tool surface: update [frontend.md](frontend.md) and the owning subsystem spec.
- New static/PWA/service-worker/cache/CSP behavior: update [frontend.md](frontend.md), [runtime.md](runtime.md), and [auth-security.md](auth-security.md) when headers or trust boundaries change.
- New CLI script: update [testing-devops.md](testing-devops.md) and the owning subsystem spec.
