# Odysseus — Context Document

> Living reference for contributors and agents. When in doubt, read the source — this doc is a map, not the territory. See `docs/architecture.md` for runtime layers and request flow.

## What is Odysseus?

A self-hosted AI workspace meant to replicate the UI experience of ChatGPT/Claude but running on your own hardware, with your own data. Local-first, privacy-first, no trojan.

**Repository:** `github.com/pewdiepie-archdaemon/odysseus`
**License:** MIT
**Language:** Python 3.11+ (FastAPI backend), vanilla JavaScript/CSS (frontend)

## Glossary

Key terms used in the codebase and issues. Definitions are approximate — read the source for current behavior.

| Term | Definition | Source |
|------|-----------|--------|
| **Chat** | Conversational interface with any local or remote LLM model | `routes/chat_routes.py`, `src/chat_processor.py` |
| **Agent** | Multi-round tool-execution loop. Prefers native tool/function calling when available, falls back to fenced code blocks (`tool_name`) for cross-provider compatibility | `src/agent_loop.py` |
| **Tool Block** | Internal representation of a tool call (from native function calling or parsed code block). Unified format so the agent loop works across all providers | `src/agent_tools.py`, `src/tool_implementations.py` |
| **Cookbook** | Hardware-aware model discovery, download ranking, and serving. Scans GPU/RAM, recommends models, handles GGUF/FP8/AWQ formats via vLLM, llama.cpp, or SGLang backends | `routes/cookbook_routes.py`, `services/hwfit/` |
| **Hardware Fit (hwfit)** | Scoring system in Cookbook that ranks models by architecture age, quant format, VRAM/RAM fit, backend support, and likely serve reliability | `services/hwfit/fit.py` |
| **Deep Research** | Multi-step research runs that gather sources, read pages, and synthesize into a visual report | `services/research/` |
| **Memory** | Persistent vector-backed memory store (ChromaDB + fastembed ONNX). Stores facts about the user across sessions | `services/memory/`, `src/memory_vector.py` |
| **Skills** | User-editable instructions that extend agent capabilities. Stored per-user, loaded into agent context. Treated as untrusted data for prompt-injection safety | `services/memory/skills.py` |
| **Session** | A chat conversation container with its own model, endpoint URL, message history, and RAG toggle | `core/session_manager.py`, `core/models.py` |
| **Provider / Endpoint** | An LLM server (Ollama, vLLM, OpenAI, Anthropic, etc.) registered in Settings. Identified by its base URL | `src/llm_core.py`, `src/endpoint_resolver.py` |
| **Model Context** | Per-model metadata: token limit, supports thinking, uses `max_completion_tokens` vs `max_tokens`. Drives prompt compaction and payload building | `src/model_context.py` |
| **Session Manager** | Central persistence layer. Owns sessions, messages, settings, user auth. All DB writes flow through it | `core/session_manager.py` |
| **Agent Loop** | Streaming multi-round loop that feeds tool output back into the LLM until DONE or BLOCKED | `src/agent_loop.py` |
| **RAG** | Retrieval-augmented generation using ChromaDB vector store + keyword fallback. Toggled per-session | `src/rag_vector.py`, `src/rag_manager.py` |
| **MCP Server** | Model Context Protocol servers (browser/playwright, memory, RAG, email, image gen). Auto-registered at startup | `mcp_servers/`, `src/mcp_manager.py` |
| **Companion** | Read-only mode for secondary devices. Limited to chat view only | `companion/routes.py` |
| **Owner Scope** | Per-user data isolation gate. Each user's sessions, documents, memories, emails are isolated by owner ID. Null owners indicate admin-level shared resources | `src/auth_helpers.py`, `core/session_manager.py` |
| **Degraded State** | When optional services (ChromaDB, SearXNG, email, ntfy) are unreachable. The app continues running but features depending on that service show degraded-state warnings | throughout codebase |

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for:
- Runtime layers and request flow
- Directory structure and import conventions  
- Persistence layer details
- Known architectural debt

### Key Concepts

- **Local-first** — all data lives on disk, no cloud dependency
- **Self-hosted only** — no SaaS mode, no telemetry
- **Hybrid tool calling** — native when available, code block fallback
- **Owner isolation** — every query filters by owner ID

## Frontend Architecture

- **Vanilla JS modules** in `static/js/` — one file per feature area
- **No framework** — no React/Vue/Svelte. DOM manipulation via vanilla JS with module pattern
- **SSE streaming** for chat responses (`chatStream.js`)
- **PWA support** via `manifest.json` and `sw.js`
- **Responsive design** — mobile-first CSS with `@media` overrides

## Testing

- **pytest** in `tests/` — unit tests for core logic, route handlers, security gates
- **JS validation** via `node --check static/js/<file>.js`
- Tests cover: auth session revocation, owner scope isolation, prompt injection guards, rate limiting, model context detection, search ranking, task scheduler delivery

## Conventions

- Python files follow standard PEP 8. Type annotations are sparse — most functions are unannotated.
- Routes are split per feature area in `routes/` (one file per domain: `chat_routes.py`, `memory_routes.py`, etc.)
- Services encapsulate complex business logic in `services/`
- Frontend modules use IIFE/module pattern, not ES modules
- All user data is gitignored via `.gitignore` (`data/`, `logs/`, `.env`)
