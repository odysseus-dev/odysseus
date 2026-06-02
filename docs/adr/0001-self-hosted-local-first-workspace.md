# ADR-0001: Self-Hosted Local-First Workspace

**Status:** Accepted
**Date:** 2024 (project inception)
**Supersedes:** None
**Complemented by:** ADR-0002 (codebase structure and layer conventions)

## Decision

Odysseus is a self-hosted, local-first AI workspace. All user data lives on disk. No cloud dependency, no telemetry, no analytics. LLM providers are optional endpoints the user configures themselves.

## Consequences

- **No SaaS mode** — the app has no server-side account system beyond local auth.
- **Degraded state over hard failure** — optional services (ChromaDB, SearXNG, ntfy) can be unreachable without crashing. Features show degraded-state warnings instead.
- **Hybrid tool calling** — prefers native function calling when available, falls back to fenced code blocks for cross-provider compatibility.
- **Provider detection by URL** — the LLM core identifies provider type from endpoint URL, building correct payloads without explicit provider selection.
- **SQLite primary store** — sessions, messages, users, settings all in SQLite. ChromaDB handles vector embeddings separately.
