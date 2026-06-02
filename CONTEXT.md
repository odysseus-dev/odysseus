# Odysseus — Context Document

## What is Odysseus?

A self-hosted AI workspace meant to replicate the UI experience of ChatGPT/Claude but running on your own hardware, with your own data. Local-first, privacy-first, no trojan.

**Repository:** `github.com/pewdiepie-archdaemon/odysseus`
**License:** MIT
**Language:** Python 3.11+ (FastAPI backend), vanilla JavaScript/CSS (frontend)

## Glossary

| Term | Definition | Don't say |
|------|-----------|-----------|
| **Chat** | Conversational interface with any local or remote LLM model | "conversation", "dialogue" |
| **Agent** | Multi-round tool-execution loop where the LLM decides which tools to use via fenced code blocks | "assistant mode", "automation" |
| **Tool Block** | A fenced code block (`` ```tool_name ``) that the agent loop parses and executes automatically | "function call", "action" |
| **Cookbook** | Hardware-aware model discovery, download ranking, and serving. Scans GPU/RAM, recommends models, handles GGUF/FP8/AWQ formats via vLLM, llama.cpp, or SGLang backends | "model marketplace", "store" |
| **Hardware Fit (hwfit)** | The scoring system in Cookbook that ranks models by architecture age, quant format, VRAM/RAM fit, backend support, and likely serve reliability | "compatibility score" |
| **Deep Research** | Multi-step research runs that gather sources, read pages, and synthesize into a visual report | "web research", "search" |
| **Memory** | Persistent vector-backed memory store (ChromaDB + fastembed ONNX). Stores facts about the user across sessions. | "knowledge base", "RAG context" |
| **Skills** | User-editable instructions that extend agent capabilities. Stored per-user, loaded into agent context. Treated as untrusted data for prompt-injection safety. | "plugins", "extensions" |
| **Session** | A chat conversation container with its own model, endpoint URL, message history, and RAG toggle | "thread", "conversation" |
| **Provider / Endpoint** | An LLM server (Ollama, vLLM, OpenAI, Anthropic, etc.) registered in Settings. Identified by its base URL. | "backend", "API key" |
| **Model Context** | Per-model metadata: token limit, supports thinking, uses `max_completion_tokens` vs `max_tokens`. Drives prompt compaction and payload building. | "model config" |
| **Session Manager** | The central persistence layer (`core/session_manager.py`). Owns sessions, messages, settings, user auth. All DB writes flow through it. | "database", "ORM" |
| **Agent Loop** | The streaming multi-round loop (`src/agent_loop.py`) that feeds tool output back into the LLM until it declares DONE or BLOCKED | "repl", "tool loop" |
| **RAG** | Retrieval-augmented generation using ChromaDB vector store + keyword fallback. Toggled per-session. | "document search" |
| **MCP Server** | Model Context Protocol servers (browser/playwright, memory, RAG, email, image gen). Auto-registered at startup. | "integration server" |
| **Companion** | Read-only mode for secondary devices. Limited to chat view only. | "mobile mode", "lite" |
| **Owner Scope** | Per-user data isolation gate. Each user's sessions, documents, memories, emails are isolated by owner ID. Null owners indicate admin-level shared resources. | "user scope", "tenant" |
| **Degraded State** | When optional services (ChromaDB, SearXNG, email, ntfy) are unreachable. The app continues running but features depending on that service show degraded-state warnings. | "error mode", "partial failure" |

## Architecture

```
app.py                   # FastAPI entry point — mounts all routes, starts background tasks
core/                    # Foundation layer
  auth.py                # Authentication, session tokens, 2FA
  database.py            # SQLAlchemy models (User, McpServer, etc.)
  session_manager.py     # Central persistence — sessions, messages, settings, users
  models.py              # Pure data containers (ChatMessage, Session)
  middleware.py           # Request middleware chain
src/                     # Business logic
  llm_core.py            # LLM call/stream with provider detection, fallback, caching
  agent_loop.py          # Multi-round tool execution loop
  agent_tools.py         # Tool block parsing, execution, schemas
  chat_processor.py      # Chat message processing pipeline
  memory_vector.py       # Vector memory operations (ChromaDB)
  model_context.py       # Per-model token estimation and capability detection
  prompt_security.py     # Untrusted context wrapping for skills/memories/docs
  tool_security.py       # Per-user tool blocking based on owner scope
routes/                  # FastAPI route modules (one per feature area)
services/                # Feature services (docs, memory, research, search, shell, stt, tts, youtube, hwfit, faces)
mcp_servers/             # Built-in MCP server implementations
static/                  # Frontend — index.html, app.js, style.css, js/*.js modules
scripts/                 # CLI tools (odysseus, odysseus-backup, odysseus-cookbook, etc.)
companion/               # Read-only companion mode routes
```

### Data Flow

1. **Chat request** → `routes/chat_routes.py` → `src/chat_processor.py` → `src/llm_core.py` (stream or call) → SSE back to frontend
2. **Agent request** → `routes/chat_routes.py` → `src/agent_loop.py` → parses tool blocks → executes via `src/agent_tools.py` → feeds output back to LLM → loops until DONE/BLOCKED
3. **Memory lookup** → `services/memory/` → ChromaDB vector search + keyword fallback → returns ranked memories for injection into context
4. **Deep Research** → `services/research/` → multi-step web crawl + synthesis → visual report output

### Persistence

- **SQLite** (`data/app.db`) — sessions, messages, users, settings, MCP servers
- **ChromaDB** (`data/chroma/`) — vector embeddings for memory and RAG
- **JSON files** — `data/memory.json`, `data/presets.json`, `data/settings.json`
- **File system** — `data/uploads/`, `data/personal_docs/`, `data/huggingface/` (Cookbook models)

### Key Design Decisions

- **Local-first** — all data lives on disk, no cloud dependency. Providers are optional endpoints.
- **Self-hosted only** — no SaaS mode, no telemetry, no analytics.
- **Tool blocks over function calling** — agent tools use fenced code blocks (`` ```tool_name ``) rather than OpenAI-style function calls. This works uniformly across all providers regardless of native function-calling support.
- **Provider detection by URL** — `src/llm_core._detect_provider()` identifies the provider from the endpoint URL and builds the correct payload format. No explicit provider type selection needed.
- **Degraded state, not hard failure** — optional services (ChromaDB, SearXNG, ntfy) can be unreachable without crashing the app. Features show degraded-state warnings instead.

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

- Python files follow standard PEP 8. No type annotations on most functions yet.
- Routes are split per feature area in `routes/` (one file per domain: `chat_routes.py`, `memory_routes.py`, etc.)
- Services encapsulate complex business logic in `services/`
- Frontend modules use IIFE/module pattern, not ES modules
- All user data is gitignored via `.gitignore` (`data/`, `logs/`, `.env`)
