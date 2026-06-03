# External Integrations

**Analysis Date:** 2026-06-03

Odysseus is local-first: most "integrations" are user-configured endpoints stored
in the local SQLite DB (with secrets Fernet-encrypted at rest), not hard-wired
cloud SaaS. All outbound calls go through `httpx`.

## APIs & External Services

**LLM Providers (OpenAI-compatible by default, auto-detected by hostname):**
- Provider detection in `src/llm_core.py` (`_detect_provider`, `_provider_label`). Endpoints configured per-row in the `model_endpoints` table (`core/database.py` `ModelEndpoint`, `api_key` stored encrypted).
  - Ollama (native `/api/chat`, including Ollama Cloud `ollama.com`) — `src/llm_core.py`
  - Anthropic (`anthropic.com`) — Bearer → `x-api-key` conversion, OpenAI→Anthropic message shaping (`src/llm_core.py`)
  - OpenAI (`openai.com`) — `OPENAI_API_KEY`
  - OpenRouter (`openrouter.ai`) — adds `HTTP-Referer` + `X-OpenRouter-Title` headers
  - Groq (`groq.com`), xAI (`x.ai`), Mistral (`mistral.ai`), DeepSeek (`deepseek.com`), Google (`googleapis.com`), Together (`together.xyz`/`together.ai`), Fireworks (`fireworks.ai`)
  - LM Studio / vLLM / llama.cpp / any OpenAI-compatible local server (fallback `openai` provider)
- Model discovery via `/v1/models` probing across configured hosts (`src/model_discovery.py`, `src/endpoint_resolver.py`).

**Self-hostable app integrations (preset catalog in `src/integrations.py` `INTEGRATION_PRESETS`):**
- Miniflux (RSS, `X-Auth-Token` header)
- Gitea (git forge, `Authorization: token ...`)
- Linkding (bookmarks, `Authorization: Token ...`)
- Home Assistant (smart home, Bearer)
- ntfy (push notifications, no auth)
- Vaultwarden (Bitwarden-compatible vault, Bearer via OAuth client-credentials)
- FreshRSS (RSS)
- Generic: arbitrary base URL + auth (`header` / `bearer` / `none`) configurable; instances + encrypted secrets persisted to `data/integrations.json` and the `integrations` DB table.

**Search providers (`services/search/providers.py`, dispatched in `services/search/core.py`):**
- SearXNG (default, self-hosted, no key) — `SEARXNG_INSTANCE`
- Brave Search (key required)
- DuckDuckGo (optional `duckduckgo-search` dep)
- Google PSE (key + `google_cx`)
- Tavily (key)
- Serper (key)
- Legacy/optional SerpAPI key + Google API key fields in `src/config.py` `SearchConfig`.

**Speech (STT/TTS):**
- STT (`services/stt/stt_service.py`): local `faster-whisper`, OR any OpenAI-compatible `/audio/transcriptions` endpoint (`endpoint:<id>`), OR browser.
- TTS (`services/tts/tts_service.py`): local Kokoro-82M (GPU), OR OpenAI-compatible `/audio/speech` endpoint, OR browser.

**Image generation (`mcp_servers/image_gen_server.py`, `routes/gallery_routes.py`):**
- OpenAI Images (`gpt-image-1.5`, `gpt-image-1`, `dall-e-3`/`dall-e-2`) — generation + inpaint/edit.
- Any OpenAI-compatible / Stable Diffusion–style diffusion server (inpaint proxy). API keys pulled from the matching `model_endpoints` row.

**Media:**
- YouTube transcripts via `youtube-transcript-api` (`src/youtube_handler.py`, `services/youtube/`). No official YouTube API key.

## Data Storage

**Databases:**
- SQLite (default) via SQLAlchemy — `DATABASE_URL` (default `sqlite:///./data/app.db`), `core/database.py:27`. `check_same_thread=False`; `PRAGMA foreign_keys=ON` enforced per-connection. Non-SQLite URLs are supported by the engine but SQLite is the shipped/assumed backend (extensive in-code `ALTER TABLE` migrations are SQLite-specific).
- Schema-managed via hand-rolled idempotent migrations in `core/database.py` (no Alembic). Tables include `sessions`, `chat_messages`, `documents`/`document_versions`, `gallery_*`, `email_accounts`, `model_endpoints`, `mcp_servers`, `scheduled_tasks`/`task_runs`, `memories`, `api_tokens`, `webhooks`, `crew_members`, `signatures`, `user_tools`.

**Vector store:**
- ChromaDB (standalone HTTP service) via `chromadb-client` — `src/chroma_client.py`, `CHROMADB_HOST`/`CHROMADB_PORT`. Used for RAG (`src/rag_vector.py`), semantic memory (`src/memory_vector.py`), and tool selection (`src/tool_index.py`).
- Local fallback: `fastembed` ONNX embeddings + on-disk vectors under `data/memory_vectors/`, `data/rag/`, `data/chroma/`. Degrades to keyword search if vectors unavailable.

**File Storage:**
- Local filesystem only. `data/` subtree (created by `setup.py`): `uploads/`, `personal_docs/`, `personal_uploads/`, `tts_cache/`, `generated_images/`, `deep_research/`, `chroma/`, `rag/`, `memory_vectors/`.

**Caching:**
- On-disk search cache (`services/cache/search/`, `services/search/cache.py`).
- TTS cache (`data/tts_cache/`).

## Authentication & Identity

**Auth Provider:**
- Custom multi-user auth (`core/auth.py`). Config in `data/auth.json`.
  - Passwords: `bcrypt` hashing.
  - Sessions: server-side opaque session tokens persisted to `data/sessions.json`, delivered as cookies (`SECURE_COOKIES` toggle).
  - 2FA: TOTP via `pyotp` + `qrcode` provisioning.
  - Enforced by `AuthMiddleware` (`app.py:360`, `core/middleware.py`); dev-only loopback bypass via `LOCALHOST_BYPASS`.
- API tokens for external automation (n8n / Make etc.): `api_tokens` table (`core/database.py`), hashed + scoped (`scopes`, default `chat`). Routes: `routes/api_token_routes.py`.

**Email/CalDAV credentials:**
- IMAP/SMTP passwords (`email_accounts` table) and signatures stored Fernet-encrypted (`EncryptedText` / `src/secret_storage.py`, key at `data/.app_key` mode 0600).

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry/Datadog/etc.). Errors surfaced via Python `logging` and user-facing messages.

**Logs:**
- Python `logging` to `logs/` directory. Diagnostics route: `routes/diagnostics_routes.py`. Readiness probe: `src/readiness.py`.

## CI/CD & Deployment

**Hosting:**
- Self-hosted via Docker Compose (`docker-compose.yml`; loopback-bound by default). GPU overlays: `docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`. Linux systemd unit `odysseus-ui.service`. macOS/Windows native launchers.

**CI Pipeline:**
- GitHub Actions, governance-only: `.github/workflows/issue-description-check.yml`, `.github/workflows/pr-description-check.yml`. No automated test/build/lint workflow detected.

## Environment Configuration

**Required env vars (see `.env.example`):**
- LLM: `LLM_HOST`, `LLM_HOSTS`, `OLLAMA_BASE_URL`, `LM_STUDIO_URL`, `OPENAI_API_KEY` (optional), `RESEARCH_LLM_ENDPOINT`.
- Search: `SEARXNG_INSTANCE`, `SEARXNG_SECRET` (optional).
- Storage: `DATABASE_URL`, `CHROMADB_HOST`, `CHROMADB_PORT`.
- RAG: `EMBEDDING_URL`, `EMBEDDING_MODEL`, `FASTEMBED_MODEL`, `FASTEMBED_CACHE_PATH`.
- Auth/security: `AUTH_ENABLED`, `LOCALHOST_BYPASS`, `SECURE_COOKIES`, `ALLOWED_ORIGINS`, `ODYSSEUS_ADMIN_USER`, `ODYSSEUS_ADMIN_PASSWORD`.
- Runtime: `APP_BIND`, `APP_PORT`, `ODYSSEUS_INPROCESS_POLLERS`, `ODYSSEUS_INPROCESS_TASKS`, `ODYSSEUS_SCRIPT_HOST`, `CLEANUP_INTERVAL_HOURS`.
- Notifications: `NTFY_BIND`, `NTFY_BASE_URL`.

**Secrets location:**
- `.env` (gitignored). Per-row secrets (LLM keys, integration tokens, email/CalDAV passwords, signatures) Fernet-encrypted in SQLite via `src/secret_storage.py`; encryption key at `data/.app_key` (mode 0600, gitignored). **Never read `.env` or `data/.app_key` contents.**

## Webhooks & Callbacks

**Incoming:**
- Scheduled-task webhook triggers: `POST /api/tasks/{task_id}/webhook/{token}` (`routes/task_routes.py:873`); per-task token via `secrets.token_urlsafe(32)`, regenerate at `.../webhook-regenerate`.
- External chat API: `POST /v1/chat` (`routes/webhook_routes.py:232`) — for n8n/Make-style automation, authenticated by scoped API token.
- MCP SSE/stdio servers connect outward (`src/mcp_manager.py`); built-in MCP servers in `mcp_servers/` (email, image gen, memory, rag) plus optional Browser MCP via `npx @playwright/mcp`.

**Outgoing:**
- Event webhooks (`webhooks` table, `src/webhook_manager.py`, `routes/webhook_routes.py`): fired on configured event types, signed with HMAC-SHA256 in header `X-Odysseus-Signature` (`src/webhook_manager.py:218`).
- ntfy push notifications (outbound HTTP POST to ntfy topic).
- Email send via SMTP; email receive via IMAP polling (`routes/email_pollers.py`, `routes/email_helpers.py`).
- CalDAV PROPFIND/REPORT sync + write-back (`src/caldav_sync.py`, `src/caldav_writeback.py`).

---

*Integration audit: 2026-06-03*
