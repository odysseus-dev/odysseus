# ADR-0001: Self-Hosted Local-First Workspace

**Status:** Accepted
**Date:** 2024 (project inception)
**Context:** Project design philosophy

## Decision

Odysseus is a self-hosted, local-first AI workspace. All user data lives on disk. No cloud dependency, no telemetry, no analytics. LLM providers are optional endpoints the user configures themselves.

## Consequences

- **No SaaS mode** — the app has no server-side account system beyond local auth.
- **Degraded state over hard failure** — optional services (ChromaDB, SearXNG, ntfy) can be unreachable without crashing. Features show degraded-state warnings instead.
- **Tool blocks over function calling** — agent tools use fenced code blocks (`` ```tool_name ``) rather than OpenAI-style function calls for cross-provider compatibility.
- **Provider detection by URL** — the LLM core identifies provider type from endpoint URL, building correct payloads without explicit provider selection.
- **SQLite primary store** — sessions, messages, users, settings all in SQLite. ChromaDB handles vector embeddings separately.
