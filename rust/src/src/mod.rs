// src/mod.rs  <- the Python `src/` package
//! Mirrors the Python `src/` package. (Populated module-by-module.)

pub mod agent_tools;
pub mod api_key_manager;
pub mod app_helpers;
pub mod assistant_log;
pub mod auth_helpers;
// `chat_helpers` <- src/chat_helpers.py — the pure URL-extraction slice
// (`extract_urls`) consumed by chat_processor/chat_handler. Default build. The
// request-layer validators (`validate_message`, `coerce_message_and_session`)
// land with the routes pass.
pub mod chat_helpers;
// `config` <- src/config.py — the pydantic-BaseSettings application config.
// Plain structs (DataConfig/LLMConfig/SearchConfig/SecurityConfig/AppConfig)
// with Default impls reproducing the field defaults + `from_env` constructors
// reading the per-class `env_prefix` env vars; a `Lazy<AppConfig>` `CONFIG`
// global and `create_directories()` / `validate_config()` free fns. Vestigial
// (no in-batch consumer); `base_dir` derives from core::constants::BASE_DIR.
// Default build. DRIFT: pydantic env_prefix -> explicit getenv; validate_config
// is NOT run at module load (Rust has no import-time execution).
pub mod config;
pub mod constants;
pub mod context_budget;
pub mod context_compactor;
// `email_thread_parser` <- src/email_thread_parser.py — the server-side email
// thread parser (plaintext `>`-prefix + multilingual attribution walker AND the
// HTML blockquote-nesting path). Default build (pure CPU/text). The HTML path
// uses `scraper` (html5ever) + `ego-tree` to reimplement bs4 `.extract()` /
// `.decode_contents()` via detached-NodeId tracking + a custom inner-HTML
// serializer; the lookahead-bearing meta regexes use `fancy-regex`. DRIFT:
// html5ever (HTML5 spec) vs Python `html.parser` (lenient) DOM shapes on
// malformed mail; Python `str.splitlines()` unicode boundaries are reproduced
// by the `splitlines` helper.
pub mod email_thread_parser;
// `preset_manager` <- src/preset_manager.py — the JSON-file-backed chat-preset
// store. Default build. DEFAULT_PRESETS is a `json!` Lazy (verbatim prompts /
// numbers); `load` ports the legacy-custom migration; `save` writes 2-space
// (`indent=2`) JSON; the store is a `serde_json::Map` (preserve_order) for dict
// ordering. Implements `chat_handler::PresetManager` (that trait lives in
// chat_handler; everything is always compiled — no cargo feature flags).
pub mod preset_manager;
// `database` <- src/database.py — the thin re-export shim. The Python file is a
// pure `from core.database import *` pass-through so `from src.database import X`
// keeps working; the Rust analogue `pub use`s the symbols `crate::core::database`
// provides (free fns + the `Session` row struct) plus `ChatMessage` from
// `crate::core::models` (where the Rust port keeps that row struct). The
// SQLAlchemy ORM model classes (`Webhook`/`ScheduledTask`/`Document`/
// `ModelEndpoint`/…) and SQLAlchemy plumbing (`Base`/`TimestampMixin`/`engine`/
// `get_db`/`get_db_session`) are NOT ported — the port uses raw rusqlite via
// `session_local()`.
pub mod database;
pub mod endpoint_resolver;
pub mod exceptions;
pub mod goal_based_extractor;
pub mod llm_core;
// `memory` — the JSON-file MemoryManager + tokenize/get_text_similarity. Pure,
// self-contained, default build (no external services, no DB). Faithful port
// including the source-default drift (_validate "unknown" vs save "user") and
// the bullet-regex group(1)=None faithful bug in extract_memory_from_chat.
pub mod memory;
pub mod model_context;
pub mod prompt_security;
pub mod rate_limiter;
pub mod request_models;
pub mod research_utils;
pub mod search;
pub mod secret_storage;
// `settings` <- src/settings.py — the JSON-read slice: DEFAULT_SETTINGS /
// DEFAULT_FEATURES, TTL-cached load_settings/load_features merge-over-defaults,
// get_setting/get_feature, atomic save_*. Default build. Unblocks the
// vision_enabled / teacher_* / research_max_tokens reads across the batch.
// `get_user_setting` (routes.prefs_routes) is deferred to the routes pass.
pub mod settings;
// `setup` <- setup.py — the first-time setup script, ported as the
// `odysseus setup` SUBCOMMAND (main.rs dispatches it like `mcp-server <id>`).
// `run()` does create_dirs (the 11 dirs), create_env (.env from .env.example),
// the tmux presence check, init_database (DATABASE_URL default + init_db), and
// create_default_admin (write the minimal <DATA>/auth.json shape; skip if it
// exists). Always compiled (no cargo feature flags): the subcommand dispatch
// lives in `main.rs`, and `init_db` is part of the same always-built database
// stack. DOCUMENTED IMPROVEMENTS: DATA_DIR honors ODYSSEUS_DATA_DIR; init_db is
// a superset of Base.metadata.create_all (it also runs migrations); the Python
// package-import check is dropped (deps are compiled in).
pub mod setup;
pub mod text_helpers;
// The interdependent tool-calling cluster. `tool_parsing` + `tool_schemas` are
// the pure-logic foundation (default build): `tool_parsing` owns the canonical
// `_TOOL_NAME_MAP` + the parse/strip functions; `tool_schemas` owns
// `FUNCTION_TOOL_SCHEMAS` + `function_call_to_tool_block`. There is no Rust
// module-load cycle even though they reference each other. The async/db parts
// (tool_execution, tool_implementations, agent_loop) land in a later pass behind
// `web`/`db`. `agent_tools` (the facade: constants, TOOL_TAGS, ToolBlock, the
// MCP-manager global, `_truncate`) is done and self-contained.
// `teacher_escalation` <- src/teacher_escalation.py — the SPLIT-gate port. The
// PURE Tier-1 detection layer (is_self_hosted, evaluate_turn_regex,
// _extract_skill_json, _format_trace, the _SOTA_HOSTS set, the two regex
// pattern lists, the prompt consts) is the live, tested value of this module.
// The async entrypoints (_call_teacher, escalate_and_learn, maybe_escalate,
// run_teacher_inline) live in the same file and are always compiled — the crate
// has no cargo feature flags. They are faithful-but-inert, because the
// teacher-endpoint resolver (ai_interaction._resolve_model) is unported (honest
// defer to None) and do_manage_skills is the existing honest stub.
pub mod teacher_escalation;
pub mod tool_parsing;
pub mod tool_schemas;
pub mod tool_security;
pub mod topic_analyzer;
pub mod url_safety;
pub mod url_security;
// `visual_report` <- src/visual_report.py — the deep-research HTML report
// generator. Pure CPU/string, DEFAULT build. Deliberate markdown engine swap
// (python-markdown + Pygments codehilite -> comrak): no codehilite syntax
// spans, otherwise a faithful 1:1 port (template, per-category CSS, TOC, image
// injection, sources panel, embedded JS). `html.escape` -> the html-escape
// crate's `encode_quoted_attribute` (exact five-entity table incl `'`->`&#x27;`);
// the bare-URL autolink's negative lookbehinds use fancy-regex; `urlparse`
// hostname via the `url` crate; `datetime.now()` is naive local time.
pub mod visual_report;
// `youtube_min` <- src/youtube_handler.py (pure slice) — is_youtube_url /
// extract_youtube_id, format_transcript_for_context /
// format_comments_for_context, YOUTUBE_INSTRUCTION_PROMPT. Default build. The
// network fetchers (extract_transcript_async / fetch_youtube_comments) are
// honest stubs returning the Python failure shape (yt-dlp + youtube-transcript-api
// not ported).
pub mod youtube_min;
// `youtube_handler` <- src/youtube_handler.py — provenance alias. The Python
// module was ported as `youtube_min` (the pure slice + honest network stubs);
// this is a 1-line `pub use youtube_min::*` so the original module name
// resolves to its faithful Rust counterpart. Default build.
pub mod youtube_handler;

// The async/db members of the tool-calling cluster. `tool_parsing`
// + `tool_schemas` above are the pure-logic foundation; these consume them.
// `ai_interaction` is the dispatch_ai_tool shim `tool_execution` needs;
// `tool_execution` is the dispatcher + result formatter + streaming subprocess
// runner. `tool_execution` calls into
// `crate::src::tool_implementations::do_*`; the crate has no cargo feature
// flags, so that sibling module is always compiled and linked.
pub mod ai_interaction;
// `embeddings` <- src/embeddings.py — the embedding backends for RAG + memory
// vector search. The HTTP API client (`EmbeddingClient`) is a faithful reqwest
// port (batch 64, OpenAI data-sort-by-index, L2 normalize, lazy dim probe);
// `FastEmbedClient` (local ONNX) is an honest stub that always raises so the
// `get_embedding_client` factory falls through to `None` like Python's
// both-backends-failed path. The persisted-endpoint file is source-relative
// (<repo>/data/embedding_endpoint.json), NOT ODYSSEUS_DATA_DIR. web (reqwest).
pub mod embeddings;
// `image_edit` — pure CPU image-editing helpers (image/imageops; no model, no
// network, no async) backing the gallery editor's rotate / sharpen / inpaint-mask /
// composite / enhance-fallback ops in routes/gallery_routes.rs.
pub mod image_edit;
// `image_models` — ort/ONNX runners + DATA_DIR/onnx_models download-to-cache helper
// backing the gallery editor's ML ops (remove-bg u2net / upscale-local + denoise
// Real-ESRGAN / enhance-face GFPGAN) in routes/gallery_routes.rs.
pub mod image_models;
// `vector_store` <- src/chroma_client.py — the DELIBERATE ChromaDB->SQLite swap.
// A `VectorCollection` facade over the shared `vectors` table (declared in
// core/database.rs) reproducing the ChromaDB collection surface
// (get_or_create / count / get / add / query / delete + delete_collection) with
// brute-force in-Rust cosine (1 - dot over L2-normalized vectors) and the
// ChromaDB `where` filter dict (owner/source/directory eq + source $contains).
// Standalone backend shared by rag_vector / memory_vector; query vectors come in
// as `&[f32]` (the embedding HTTP client is `embeddings.rs`). Always compiled
// (no feature flags): pulls in the rusqlite layer and sits with the vector cohort.
pub mod vector_store;
// `rag_vector` <- src/rag_vector.py — vector RAG on the SQLite vector store +
// HTTP embeddings (PORT_VIA_DB). `VectorRAG` drives the `"odysseus_rag"`
// collection (the ChromaDB->SQLite swap) with the faithful hybrid
// 0.7*vector + 0.3*keyword score, the keyword-overlap fallback, the CHAR-based
// sentence chunker (overlap arithmetic preserved), and `os.walk`-style directory
// indexing via `walkdir`. The `.pdf` indexing branch extracts text via
// `pdf-extract` inline (the `""`-on-error analogue of personal_docs.extract_pdf_text,
// whose canonical free fn lands with that sibling module). DOCUMENTED DRIFT: the
// `doc_id = doc_{hash(text)%10^16}` hash uses a FIXED hasher (Python's hash() is
// PYTHONHASHSEED-randomized — no value to preserve); `round(.,4)` is
// half-away-from-zero. Always compiled (HTTP embeddings + rusqlite vector store).
pub mod rag_vector;
// `chroma_client` <- src/chroma_client.py — the ChromaDB singleton-client facade.
// PORT_VIA_DB. A zero-sized `ChromaClient` over the deliberate
// ChromaDB->SQLite swap: `get_or_create_collection` delegates to
// `vector_store::VectorCollection::get_or_create` (metadata/`hnsw:space`
// accepted-and-ignored — cosine is hardwired), `delete_collection` delegates to
// `vector_store::delete_collection`, and `heartbeat()` is a no-op (local SQLite,
// no service to ping). `get_chroma_client()` returns the facade; `reset_client()`
// is a no-op. DOCUMENTED intentional divergence: the Python
// chromadb-not-installed `RuntimeError` path is dead code (SQLite is always
// available); the `CHROMADB_HOST`/`CHROMADB_PORT` env vars are meaningless and
// unread.
pub mod chroma_client;
// `chroma_launch` — NEW capability (no single Python source): the optional Docker
// launcher for a REAL ChromaDB server, replicating the docker-compose.yml
// `chromadb` service via the standalone-host `docker run` command. It
// mirrors `builtin_mcp`'s detect-then-launch posture: `find_docker` (PATH +
// common-location scan), `ensure_chroma_running()` (reuse a live container /
// `docker start` a stopped one / `docker run -d` a new one, then poll
// `GET /api/v2/heartbeat` on `localhost:<CHROMADB_PORT>` until ready, with a
// pull-tolerant 90s budget), and `ChromaHandle { started_by_us }.stop()` which
// stops the container ONLY if THIS process started it (never kills a reused /
// externally-managed one — honoring compose `restart: unless-stopped`). Honest
// docker-absent message + `PyError` on timeout/non-zero exit (never a fabricated
// readiness). Wired by the `odysseus chroma [start|stop|status]` subcommand and
// the `web::run` startup auto-launch (gated by ODYSSEUS_LAUNCH_CHROMA + the chroma
// backend selection). The host-side data volume uses `core::constants::DATA_DIR`
// (honors ODYSSEUS_DATA_DIR). Readiness poll uses `reqwest::blocking`; no new crate.
pub mod chroma_launch;
// `rag_manager` <- src/rag_manager.py — the backward-compat `VectorRAG` wrapper.
// PORT_VIA_DB (web). `RAGManager` owns a `VectorRAG` by value and delegates each
// method (`search` mapping each `SearchHit` to its Python-dict shape via
// `to_value()`; `index_personal_documents`/`search` pass the `None` defaults the
// Python shim relied on). IO-bound methods + the constructor are `async` (the
// `VectorRAG` HTTP embedding probe); `rebuild_index`/`get_stats` stay sync.
// Dead/backward-compat: no in-tree caller (the live RAG entrypoints are
// `rag_singleton` — disabled — and `tool_index`'s direct `chroma_client` use).
pub mod rag_manager;
// `rag_singleton` <- src/rag_singleton.py — the application RAG singleton.
// PORT_VIA_DB (web). `get_rag_manager()` returns `None` (the faithful disabled
// behaviour: VectorRAG/ChromaDB is unused and re-attempting a broken init every
// 30s hung). `_get_rag_manager_legacy()` is the original lazy initializer, ported
// faithfully but `#[allow(dead_code)]` (never called, exactly like Python):
// `Lazy<Mutex<Option<Arc<VectorRAG>>>>` cache + `Lazy<Mutex<Option<Instant>>>`
// 30s retry throttle, `BASE_DIR/data/rag` persist dir, `.healthy()` gate. The
// Python `ImportError` branch is dead in Rust (no dynamic import to fail).
pub mod rag_singleton;
// `tool_index` <- src/tool_index.py — RAG-based tool selection for agent mode.
// PORT_VIA_DB (web). The canonical home for `ALWAYS_AVAILABLE` /
// `ASSISTANT_ALWAYS_AVAILABLE` / `COLLECTION_NAME` / `BUILTIN_TOOL_DESCRIPTIONS`
// / `_KEYWORD_HINTS` / `_SCHEDULE_RE` / `get_tools_for_query`. `ToolIndex` drives
// the `"odysseus_tool_index"` collection over `chroma_client` + a shared
// `Arc<EmbeddingClient>`; `index_builtin_tools` (stale-prune + upsert + sha256
// fingerprint), `index_mcp_tools` (gen=0 constant — McpManager has no
// `_generation`, so reindex-once-then-noop, the faithful Python `getattr` default),
// `retrieve` (first-seen-order dedupe), `get_tool_index()` 30s-throttled singleton.
// The numpy branch is dropped (`EmbeddingClient::encode` already returns normalized
// `Vec<Vec<f32>>`). NOTE: `agent_loop.rs` keeps its own faithful private copies of
// `ALWAYS_AVAILABLE`/`_KEYWORD_HINTS` (it takes the keyword-fallback path); an
// optional future de-dup could import from here.
pub mod tool_index;
// `memory_vector` <- src/memory_vector.py — vector index over memory entries
// (PORT_VIA_DB). `MemoryVectorStore` drives the `"odysseus_memories"` collection
// on the same SQLite vector store + a shared/lazy `Arc<EmbeddingClient>`. Pure
// backend swap: faithful add/remove/count/search (score = round(1 - distance, 4))
// / find_similar (0.92 near-dup threshold) / rebuild (batches of 100). Same
// half-away-from-zero round() drift. Always compiled (no cargo feature flags).
pub mod memory_vector;
pub mod tool_implementations;
pub mod tool_execution;
// `agent_loop` — the round-based streaming loop + system-prompt assembly. Its
// async core (`stream_agent_loop`, `_build_system_prompt`, `_run_verifier_subagent`,
// `_load_mcp_disabled_map`) and the pure helpers (`_detect_admin_intent`,
// `_assemble_prompt`, `_compute_final_metrics`, …) are all always compiled — the
// crate has no cargo feature flags — and the pure-logic unit tests run alongside
// them. The loop consumes `stream_llm_with_fallback` / `execute_tool_block` /
// the `do_*` cluster, all of which are likewise always compiled.
pub mod agent_loop;
// `mcp_manager` <- src/mcp_manager.py — the MCP (Model Context Protocol) tool-
// server manager. PORT_PARTIAL: the stdio transport is hand-rolled as
// newline-delimited JSON-RPC over the child's stdin/stdout (the core/codex.rs
// precedent; there is no `mcp` Rust crate dep), covering initialize +
// notifications/initialized + tools/list + tools/call with content blocks as a
// typed enum. All pure helpers (schemas/prompt/statuses/is_builtin + the
// 120-char-trunc prompt cache) are faithful; connect_all_enabled is a raw SQL
// SELECT on mcp_servers. DEFERRED honest stubs: _connect_sse (SSE/HTTP/OAuth
// transport) and _reconnect_builtin (unported src/builtin_mcp + python server
// scripts) — the latter degrades call_tool's builtin auto-reconnect to the plain
// error path. Always compiled (tokio::process child + rusqlite SELECT). Standalone this
// pass: NOT wired into agent_tools::_MCP_MANAGER / tool_execution (a separate,
// flagged decision; both already tolerate an absent manager).
pub mod mcp_manager;
// `builtin_mcp` <- src/builtin_mcp.py — auto-registration of built-in MCP servers
// at startup. PORT_PARTIAL. `_find_npx` (PATH scan + common-location +
// node-sibling fallback), the `_BUILTIN_SERVERS` / `_BUILTIN_NPX_SERVERS` tables,
// and the `MCP_DISABLED` (ODYSSEUS_DISABLE_MCP) flag are faithful. The npx browser
// server is portable and is connected via `McpManager::connect_server` with the
// same 3s start delay + 30s timeout. DOCUMENTED DEFER: the four Python-script
// servers (mcp_servers/*.py, unported) always take the honest missing-script
// warning branch — never a fake connection; `sys.executable` has no Rust analogue
// and is omitted. No in-tree caller (Python invokes it from app bootstrap), so
// `register_builtin_servers` is `#[allow(dead_code)]`.
pub mod builtin_mcp;
// `bg_jobs` <- src/bg_jobs.py — detached background-`bash` job launch + the
// restart-safe, disk-derived status machine. Faithful port: the on-disk store
// (`bg_jobs.json`) + per-job `.cmd.sh`/`.sh`/`.log`/`.exit` files match the
// Python wire-for-wire. Detached process groups use `os.setsid`/`os.killpg`/
// `os.kill(pid, 0)` (Unix-only, via the `nix` crate; `#[cfg(unix)]` is a
// platform gate, not a cargo feature). It lands with the subprocess tooling
// cohort (the `tool_execution` background path).
// Documented deviation: it reads the **plain** `DATA_DIR` env var (default
// `"data"`), NOT the crate-wide `ODYSSEUS_DATA_DIR`, matching the Python source.
#[cfg(unix)]
pub mod bg_jobs;
// `integrations` <- src/integrations.py — user-registered HTTP API integrations.
// JSON-file registry (load/save/get/add/update/delete + _find_integration), the
// INTEGRATION_PRESETS ordered map (Lazy IndexMap, insertion-order preserved),
// get_integrations_prompt, migrate_from_settings, and the async
// `execute_api_call` (httpx -> reqwest, redirect Policy::none() for httpx parity,
// header/bearer/query/basic auth, content-type formatting, 12000-char
// truncation). Only execute_api_call truly needs HTTP; the whole module is
// always compiled (no cargo feature flags). DOCUMENTED DRIFT: DATA_FILE is source-relative
// (<repo>/data/integrations.json, derived from core::constants::BASE_DIR) and
// IGNORES ODYSSEUS_DATA_DIR, matching the Python `dirname(dirname(__file__))`.
pub mod integrations;
// The PDF cluster (both always compiled — no cargo feature flags). `pdf_form_doc` <-
// src/pdf_form_doc.py is the markdown<->form-field bridge: pure markdown + JSON
// + DB (no PDF binaries) — it reuses the `documents.rs` INSERT pattern +
// `set_active_document`. Faithful PORT_NOW (no deferrals). `pdf_forms` <-
// src/pdf_forms.py is the AcroForm read/write engine: a deliberate
// PyMuPDF(`fitz`, AGPL-3.0) -> `lopdf` backend swap. `has_form_fields` /
// `extract_fields` / `fill_fields` are PORT_PARTIAL via lopdf (label inference
// DEFERRED to the field-name fallback; fill sets /V + checkbox /AS +
// /AcroForm /NeedAppearances with a documented stale-appearance caveat —
// the field VALUES are written for real). `stamp_signatures` / `stamp_annotations`
// are REAL via `pdf_render` (pdfium): text runs + checkmark polylines +
// PNG image-object stamping (`create_image_object`); they return 0 + log
// `[PDF stamping not available]` ONLY on a pdfium provisioning/open failure
// (never a fake count). The page-percent -> user-unit + font-metric geometry is
// ported in `plan_annotation`.
pub mod pdf_form_doc;
pub mod pdf_forms;
// `pdf_render` <- the PyMuPDF(`fitz`) render/geometry/positioned-text slices that
// `lopdf` cannot cover, re-backed onto `pdfium-render` (binds the prebuilt
// libpdfium dylib at runtime). Shared module the rest of the PDF cluster calls:
// it provisions PDFium once (downloads the bblanchon `pdfium-*.tgz` for the host
// platform to `DATA_DIR/pdfium`, gunzips+untars the dylib via flate2 + tar, then
// `Pdfium::bind_to_library` ONCE behind a process-global OnceCell), then exposes
// the four shared ops: `render_page_png` (fitz `get_pixmap(Matrix(scale,scale))`
// -> PNG, A), `page_geometries` (per-page `page.rect` width/height for the
// interactive viewer, B), `stamp_plans` (BURN text/checkmark/signature draws onto
// the page, the real backing for `pdf_forms::stamp_annotations`, D), and
// `page_words` (positioned text in fitz TOP-LEFT space for `_infer_label`, E).
// `document_routes.rs` (A/B/C handlers) and `pdf_forms.rs` (D/E) are the callers.
// On a provisioning failure (offline first run / unsupported platform) the public
// fns return `Err`/`Unavailable(msg)` so HTTP handlers map it to the SAME honest
// 500 the Python emits when `import fitz` fails — NEVER a faked success. All
// pdfium handles are `!Send`, so callers wrap the work in `spawn_blocking`.
// Always compiled (no cargo feature flags); pulls in pdfium/flate2/tar.
pub mod pdf_render;
// `personal_docs` <- src/personal_docs.py — personal-document indexing +
// keyword/vector retrieval. PORT_NOW for the pure slices (PersonalDocsConfig
// stop-words / extensions, read_text_file, CHAR-based split_chunks, tokenize,
// sorted-walk load_personal_index, stable-sort retrieve_personal_keyword,
// retrieve_personal) and the PersonalDocsManager directory bookkeeping
// (load/save directories + excluded files, add/remove_directory, refresh_index,
// get_stats, index_all_directories). `extract_pdf_text` is PORT_PARTIAL via the
// `pdf-extract` crate (the pypdf analogue; `""` on any failure, never
// fabricated). The RAG manager is the narrow duck-typed `RagManager` trait
// (search / index_personal_documents / rebuild_index) so `rag_vector::VectorRAG`
// can implement it without `personal_docs` taking a hard dependency on that
// sibling step; `rag_manager=None` degrades to the keyword path exactly like
// Python's `if self.rag_manager:` guards. Always compiled (pdf-extract dep + the
// vector RAG cohort). `os.path.abspath` -> `std::path::absolute` (documented).
pub mod personal_docs;
// `document_processor` <- src/document_processor.py — attachment/document
// content building for the chat path. PORT_PARTIAL. The pure file-handling
// slices are faithful: `_is_text_file`, `_process_text_file` (char-based
// truncation loop + code-fence; the `personal_docs.read_text_file` UTF-8
// `errors="ignore"` read is inlined as a lossy byte decode), and `_process_pdf`
// (text via `pdf-extract`'s `extract_text_by_pages` with the per-page
// `[Page N text]:` concat + 15000-char cap). `build_user_content` is the
// linchpin: it returns Python's `str | list` as a `serde_json::Value`, inlines
// base64 image/audio, mime-guesses via `mime_guess`, does the os.walk
// attachment-search fallback (`walkdir`), runs the PDF-form detection ->
// `pdf_forms` + sidecar + `pdf_form_doc` auto-doc + the `auto_opened_docs` DB
// SELECT. The `upload_handler` argument is taken via a small `UploadHandlerLike`
// trait so this module does not hard-depend on the sibling (not-yet-landed)
// `upload_handler.rs`. The VL layer is now REAL: `analyze_image_with_vl[_result]`
// resolves the vision model (`ai_interaction::resolve_model_spec` via
// `resolve_vl_model` + the vision fallback chain + the settings gates) and makes
// the actual `llm_call_async` call (shared `vl_chat` message builder), returning
// honest bracketed markers only on vision-disabled / no-model / all-candidates-
// fail. The ONLY remaining DEFERRED honest stub is the per-page PDF image -> VL
// OCR loop inside `_process_pdf` (no faithful pure-Rust page-image extraction).
// Always compiled (pdf-extract/mime_guess/base64/the lopdf-backed pdf_forms + the
// DB-backed pdf_form_doc).
pub mod document_processor;
// `upload_handler` <- src/upload_handler.py — upload save/list/cleanup +
// filename sanitization + content-type sniffing + file-type predicates.
// PORT_PARTIAL (web). The pure / filesystem logic is faithful: `secure_filename`
// (NFKD + ascii-ignore + `[^\w\s\-.]` strip + whitespace->`_` collapse + leading
// dot strip + `"unnamed"`), `validate_upload_id`
// (`^[0-9a-fA-F]{32}\.[A-Za-z0-9]+$`), `is_image_file`/`is_audio_file`/
// `is_document_file`/`is_safe_file_type` (extension + MIME set membership),
// `inside_base_dir` (delegates to the already-faithful `app_helpers`
// realpath+commonpath guard), `detect_content_type` (libmagic ->
// `infer` magic-byte sniff + `mime_guess` extension fallback),
// `calculate_file_hash` (SHA-256 hexdigest), `get_upload_dir` (`%Y/%m/%d`
// LOCAL-time dirs), `cleanup_old_uploads` (os.walk date-dir pruning),
// `cleanup_rate_limits` + the rate-limit `IndexMap` (Python dict insertion
// order for the size-cap eviction tie-break), `get_upload_stats`, and the
// `save_upload` pipeline (rate-limit/size/owner-scoped dedupe/write/metadata/
// `uploads.json` update; FastAPI `HTTPException` -> `crate::routes::HttpException`).
// `UploadHandler` implements `document_processor::UploadHandlerLike`, so the real
// handler now threads through `build_user_content` + `ChatHandler` (the test
// stubs in those modules stay for unit tests only). HONEST SIMPLIFICATIONS:
// `save_upload` takes the file *bytes* + filename rather than the un-portable
// FastAPI `UploadFile`; the PIL `ImageOps.exif_transpose` image-dimension capture
// uses the `image` crate's intrinsic dimensions WITHOUT applying EXIF orientation
// (90/270-deg-rotated images are not width/height-swapped). Always compiled
// (infer/mime_guess/image deps + the `UploadHandlerLike` surface).
pub mod upload_handler;
// `upload_limits` <- src/upload_limits.py — bounded read helpers for direct
// (non-multipart) upload endpoints (cap the in-memory body read).
pub mod upload_limits;
// `deep_research` <- src/deep_research.py — the IterResearch-style async engine.
// PORT_PARTIAL: the full round loop (`research`), the two `join_all`
// concurrency fan-outs (search-all / fetch-all, with `return_exceptions`
// modelled as a per-task `Result`), the `_llm` helper (`llm_call_async` +
// `strip_thinking`), and every parse/format helper (the three-tier
// `_parse_json_array` incl. truncated-array repair, `_parse_json_object`,
// `_format_findings`, `get_stats`) are faithful. Prompts are `const &str` with
// manual placeholder substitution (the templates embed literal `{{ }}` example
// JSON that `format!` would reject). Cancellation is an `Arc<AtomicBool>`.
// DEFERRED honest stubs: `_search` / `_fetch_and_extract` back the unported
// `src.search` provider/content modules, so they return no results — the engine
// then degrades to the exact "**Search unavailable**" branch Python takes when
// search is down (after `max_empty_rounds`). It NEVER fabricates a search hit.
// Always compiled (reqwest LLM + `futures_util::future::join_all`).
pub mod deep_research;
// `diffusion_server` <- scripts/diffusion_server.py — local Stable-Diffusion
// txt2img server (candle), the `odysseus diffusion-server` subcommand.
pub mod diffusion_server;
// `research_handler` <- src/research_handler.py — the research-service handler +
// task registry that wraps the `deep_research` engine. PORT_PARTIAL. The
// pure / fs / persistence layer is the live value: the task registry, the on-disk
// JSON store (`<DATA_DIR>/deep_research/<session>.json`), `_extract_sources` /
// `_extract_raw_findings`, `get_status` / `get_result` / `get_sources` /
// `get_raw_findings`, `clear_result`, `get_avg_duration`, `hide_image` /
// `unhide_all_images`, `_format_research_report`, and `get_report_html` (wired to
// the ported `crate::src::visual_report::generate_visual_report`). DOCUMENTED
// DEVIATIONS: `RESEARCH_DATA_DIR` resolves under `core::constants::DATA_DIR`
// (honors ODYSSEUS_DATA_DIR) rather than the literal Python `data/deep_research`;
// `llm_call_async`'s `max_retries=1` kwarg is dropped (single-attempt, batch-wide).
// HONEST DEFERS: the legacy `research_engine.ResearchOrchestrator` is permanently
// absent (Python's ImportError branch is the live one), so `_legacy_engine` is
// always None. NOW WIRED (services tranche): `_handle_research_failure`'s
// basic-search fallback calls the ported `crate::src::search::comprehensive_web_search`,
// and `event_bus.fire_event("research_completed", owner-or-None)` is wired to the
// ported `crate::src::event_bus` (an empty owner collapses to None, matching
// Python's `entry.get("owner") or None`). The `DeepResearcher` engine is wired through a
// `ResearchEngine` trait seam to the ported `crate::src::deep_research`. Always
// compiled (HTTP via `llm_call_async`, tokio task registry).
pub mod research_handler;
// `chat_processor` <- src/chat_processor.py — the LLM context-preface assembler.
// PORT_PARTIAL. The pure retrieval core is faithful: `content_tokens`
// (the `[a-z0-9]+(?:[-_][a-z0-9]+)*` tokenizer + len>=3 non-stopword filter),
// the `STOPWORDS` frozenset, and `ChatProcessor::hybrid_retrieve` (the BM25/IDF
// math — k1=1.5, b=0.75, idf=ln((N-df+0.5)/(df+0.5)+1), binary tf, the kw/6.0
// cap, the 1.4/1.3/1.2 identity/contact/preference boosts, the vector/no-vector
// gates and weighted blend, the >0.12 floor, the descending stable sort).
// `build_context_preface` ports the preset prompt + UNTRUSTED_CONTEXT_POLICY +
// pinned/retrieved-memory blocks (`untrusted_context_message`) + the
// `increment_uses` bump + the RAG threshold / `round(sim, 3)` / 10000-char
// truncation block + the `_last_used_memories` bookkeeping. The four duck-typed
// collaborators are modelled as narrow traits (`RagSearch` owner-aware search,
// `MemoryVectorSearch`, `SkillsIndex`) so this module does not hard-depend on
// rag_vector/memory_vector (sibling step); `memory_manager` is the real
// `crate::src::memory::MemoryManager`. HONEST DEFERS: web search
// (`comprehensive_web_search`) + URL fetch (`fetch_webpage_content`) from the
// unported `src.search` are skipped no-ops (`web_sources` empty, no `web page:`
// blocks); the agent-mode skills index never fires because the
// `services.memory.skills` SkillsManager is unported (skills_manager always None).
pub mod chat_processor;
// `chat_handler` <- src/chat_handler.py — the shared pre-processing/validation
// layer for the /api/chat + /api/chat_stream routes. PORT_PARTIAL, always compiled
// (async preprocess + DB-backed SessionManager + the document_processor free
// functions + the sibling-pass upload_handler surface). Faithful surface:
// validate_and_extract_preset (HTTPException(400) -> crate::routes::HttpException),
// enhance_message_if_needed (identity), update_session_name_if_needed,
// trim_history_if_needed (MAX_CONTEXT_MESSAGES=90), handle_memory_command
// (memory.rs + ChatMessage + session save + update_session_last_accessed), and
// the preprocess_message multimodal assembly (URL/YouTube context, vision-model
// heuristics incl the `\dvl|vl\d|[-_]vl[-_.\d]|vl-` regex, uploads.json indexing,
// the str|list content collapse as serde_json::Value). `build_user_content` +
// `analyze_image_with_vl_result` are the ported `document_processor` functions
// (the latter is the REAL vision call: resolve_vl_model -> vl_chat/llm_call_async
// with honest bracketed markers on disabled/no-model/all-fail); `ChatHandler<U>`
// is generic over the concrete
// `document_processor::UploadHandlerLike` upload handler (reused, not
// re-declared). `preset_manager` is the narrow `PresetManager` trait
// (`presets()` only). DEFERRED honestly (already inert here): the YouTube
// transcript/comments fetchers are youtube_min's failure-shape stubs
// (services.youtube unported). chat_processor / research_handler are stored to
// match __init__ but never invoked by this module (the route layer uses them).
pub mod chat_handler;
// `builtin_actions` <- src/builtin_actions.py — the registry of built-in
// automation actions the task scheduler runs without an LLM (or with a composed
// utility endpoint). PORT_VIA_DB (web). This module owns the shared `Outcome`
// enum (the Rust model of Python's `TaskNoop` / `TaskDeferred` `BaseException`
// sentinels), re-exported for `task_scheduler.rs` to match before generic
// errors. PORT_NOW: `_result_has_work` (non-overlapping `str.count` parity),
// `_classify_event_heuristic` + the `_TYPE_COLORS` / `_HEURISTIC_*` Lazy IndexMap
// tables (dict-literal first-match order), the `subprocess.run` actions
// (ssh_command / run_script / run_local via `std::process::Command` on a
// `spawn_blocking` thread), `action_tidy_research` (fs glob + JSON validate).
// PORT_VIA_DB (raw rusqlite SQL + owner WHERE): consolidate_memory (memory.rs +
// composed-endpoint LLM tidy), tidy_calendar (calendar_events dedupe + watermark
// state file), classify_events (heuristic + batch LLM), daily_brief
// (calendar_events + notes; IMAP unread/subjects DEFERRED honestly to 0),
// ping_notes (notes due-window scan + per-owner JSON state; the
// `routes.note_routes.dispatch_reminder` send is a DEFERRED honest no-op, NOT a
// fake delivery). DEFERRED honest stubs (`<action>: not available in the Rust
// port yet`, success=false, never faked): tidy_sessions / tidy_documents
// (session_actions / document_actions), the email cluster (summarize_emails /
// draft_email_replies / extract_email_events / mark_email_boundaries /
// learn_sender_signatures / check_email_urgency — IMAP + SCHEDULED_DB + MIME),
// test_skills / audit_skills (services.memory.skills + skills_routes). Endpoint
// resolution composes settings::get_setting -> model_endpoints::endpoint_auth ->
// endpoint_resolver, since `resolve_endpoint` itself is unported. `ping_events`
// / `ping_notes` are excluded from `BUILTIN_ACTIONS` (matching the Python).
pub mod builtin_actions;
// `task_scheduler` <- src/task_scheduler.py — the background scheduler that runs
// `ScheduledTask` rows (LLM / research / built-in action tasks). PORT_VIA_DB
// (web). PORT_NOW pure: `compute_next_run` (PARITY-CRITICAL date math — chrono +
// chrono-tz + the `cron` crate; DOCUMENTED DOW deviation — the cron crate uses
// 1=Sun..7=Sat and needs a leading seconds field, so croniter expressions are
// translated via `croniter_to_cron_crate` + `remap_dow_field`), the
// `format_email_output` / `is_email_output_target` helpers, and the
// `HOUSEKEEPING_DEFAULTS` / `RETIRED_HOUSEKEEPING_ACTIONS` / `SILENT_ACTIONS` /
// `MODEL_BACKED_ACTIONS` / `CHECKIN_MCP_PATTERNS` Lazy tables. PORT_VIA_DB: all
// SQLAlchemy `.query/.filter/.delete/func.count` -> hand-written rusqlite SQL +
// row mappers (`scheduled_tasks` / `task_runs` / `crew_members` / `notes` /
// `calendar_events` / `sessions` / `chat_messages` / `model_endpoints`) and the
// full executor machinery (`run_loop` / `check_due_tasks` / `execute_task[_locked]`
// / notifications / the `Semaphore(1)` model slot / `executing` async-lock set /
// the `cached` singleflight / `note_pings_loop` / `ensure_defaults` /
// `ensure_assistant_defaults` / `deliver_task_result` session path). `TaskNoop` /
// `TaskDeferred` reuse `builtin_actions::Outcome` (re-exported here);
// `_run_agent_loop` drives `stream_agent_loop` and parses its SSE events.
// `_execute_research_task` wires the ported `DeepResearcher` + persists in the
// Research-panel shape via `research_handler::{RESEARCH_DATA_DIR, extract_sources,
// extract_raw_findings}`. DEFERRED honestly: `resolve_endpoint` /
// `resolve_utility_fallback_candidates` / `llm_call_async_with_fallback` (unported
// — saved-endpoint path composed from `model_endpoints::endpoint_auth` +
// `endpoint_resolver`, fallbacks degrade to the single primary candidate),
// `tool_index` RAG select (None = use all tools), `_deliver_via_email` (SMTP /
// routes.email_* unported — logs + records `error`, never fakes "delivered"),
// `_deliver_via_mcp` (tolerates the inert MCP slot), the check-in
// integrations/MCP discovery loops (honest skip), and `event_bus` / `prefs`
// (no-op / `{}`).
pub mod task_scheduler;
// `agent_runs` <- src/agent_runs.py — the detached agent-run manager. Keeps an
// agent/chat stream running server-side after the SSE client disconnects (tab
// close / navigate / refresh): a background tokio task drains the wrapped stream
// into a per-session replay buffer; `subscribe` replays the buffer then streams
// live; the run survives subscriber loss and self-evicts 180s after the last
// subscriber on a terminal run. PORT_NOW (web): `Lazy<Mutex<HashMap>>` registry of
// `Arc<Mutex<Run>>`, per-subscriber `tokio::sync::mpsc` fan-out, an
// `async_stream::stream!` subscribe (replay + live + `(None,None)` sentinel + tail
// flush, with `seq >= next_seq` dedupe), and `Arc::ptr_eq` identity-checked
// eviction. DOCUMENTED DRIFT: cooperative cancellation via
// `tokio_util::sync::CancellationToken` (the `_drain` loop `select!`s the wrapped
// stream against it), NOT `JoinHandle::abort()` — Python's `Task.cancel()` lets
// the wrapped generator CATCH the `CancelledError` to persist its partial; the
// Rust analogue is the wrapped stream's own `Drop` running on cancel. The
// "running -> error" catch arm has no analogue (a `Stream<Item=String>` cannot
// raise — errors are terminal SSE event strings, exactly like the Python yields
// strings). Always compiled (tokio runtime + mpsc + async-stream + tokio-util).
pub mod agent_runs;
// `bg_monitor` <- src/bg_monitor.py — the always-on monitor that auto-continues
// the agent when a detached `bg_jobs` background job finishes. PORT_NOW, gated
// `#[cfg(unix)]` to match `bg_jobs` (the `nix` subprocess layer is Unix-only;
// a platform gate, not a cargo feature — the crate has no feature flags). The
// Python `asyncio`-task monitor becomes a `tokio` task:
// `start_bg_monitor` is idempotent (a `Lazy<Mutex<Option<JoinHandle>>>` guard),
// `_loop` drains `bg_jobs::pending_followups` every `POLL_INTERVAL_S=5` and only
// calls `mark_followed_up` AFTER the follow-up run succeeds (transient failures
// retry next tick). `_drain_agent` builds `AgentLoopArgs` (max_rounds=12,
// context_length=0 — the Rust Session has no such attr, matching Python's
// `getattr(..., 0) or 0`) and parses the `data: `-framed SSE into
// `(full_prose, tool_events)`. DOCUMENTED DEVIATION: this module OWNS a NEW
// concrete-`SessionManager` global (`set_session_manager`/`get_session_manager`)
// because `ai_interaction` exposes no session-manager accessor and
// `core::models::session_manager()` returns the narrow `SessionPersistence`
// trait (only `_persist_message`). `app_initializer` wires the setter. PARITY
// NOTE: `get_session` raises `KeyError` on a missing session (so the Python
// `if not sess: return True` branch is dead in CPython too) — the Rust `Err`
// propagates to `_loop`'s catch -> log + retry, the same observable outcome.
#[cfg(unix)]
pub mod bg_monitor;
// `app_initializer` <- src/app_initializer.py — startup wiring of the manager /
// handler graph. PORT_PARTIAL. `create_directories()` loops the
// `src::constants` DATA/PERSONAL/RUNBOOK/UPLOAD dirs; `initialize_managers(base_dir,
// rag_manager: Option<Arc<dyn personal_docs::RagManager>>) -> PyResult<AppManagers>`
// (async — `MemoryVectorStore::new` probes its embedding endpoint and the
// degraded-rebuild path `await`s `rebuild`) builds the concrete `AppManagers`
// struct (live Arc-wrapped objects, NOT a serde map; the vestigial
// `current_presets` / `PERSONAL_INDEX` dict entries are not mirrored). It sets
// BOTH session-manager globals: `core::models::set_session_manager` (the narrow
// `SessionPersistence` trait object enabling `Session.add_message()` persistence)
// AND — on `unix` — `bg_monitor::set_session_manager` (the concrete
// `Arc<SessionManager>` the background monitor needs; that global LIVES in
// `bg_monitor`, gated `#[cfg(unix)]`). A documented deviation from Python's
// `ai_interaction` global home. HONEST STUBS (never faked): SkillsManager
// (`services.memory.skills` unported) -> ChatProcessor built with `None` skills;
// `update_search_config` (`src.search` lacks it) -> a logged no-op when a saved
// Brave key is present; `rag_manager._model` duck-type -> `None` to
// `MemoryVectorStore::new` (same as the Python `embedding_model=None` path). The
// MemoryVectorStore degraded-rebuild logic (`healthy` gate + empty-index rebuild
// from `MemoryManager.load()`) is ported faithfully. COLLABORATOR WIRING: a
// healthy MemoryVectorStore IS wired into ChatProcessor's `MemoryVectorSearch`
// slot (via a `MemoryVectorSearchAdapter` bridging the store's `async` `search`
// into the sync trait) so chat-time semantic memory search runs, exactly like
// Python's `memory_vector=memory_vector` pass; a degraded store is `None` (Python
// sets `memory_vector = None`). The other two collaborators stay `None`:
// `rag_vector::VectorRAG` does not impl `RagSearch` (the live RAG singleton is
// disabled anyway -> Python `rag_manager` None) and the `services.memory.skills`
// SkillsManager (`SkillsIndex`) is unported. ChatHandler's stored-but-uncalled
// chat_processor/research_handler slots take a ZST `Collaborator` stub while the
// concrete objects live on `AppManagers`.
pub mod app_initializer;

// `model_discovery` <- src/model_discovery.py — vLLM / OpenAI-compatible model
// discovery across Tailscale (or `LLM_HOSTS`-env) hosts. PORT_NOW. The
// synchronous `httpx.get` fan-out over a `ThreadPoolExecutor(max_workers=50)`
// becomes a tokio `reqwest` fan-out via `futures_util::buffer_unordered(50)`
// (documented async swap; the module is always compiled and only runs in the
// async server). Tailscale discovery shells out to the `tailscale` binary via
// `tokio::process::Command` with a 5s timeout + a 60s TTL cache; a missing
// binary -> empty list. Dedupe by `(port, sorted(models))`, sort by `(host,
// port)`, static OpenAI provider list verbatim.
pub mod model_discovery;
// `webhook_manager` <- src/webhook_manager.py — outgoing webhook delivery.
// PORT_NOW (web). Pure validators / SSRF guards (the 8 private CIDR ranges are
// hand-rolled over `std::net::IpAddr` octet/segment range checks — no `ip-cidr`
// crate); `_resolve_hostname_ips` uses `ToSocketAddrs` (host, 0) and fails closed
// on resolve failure. `WebhookManager` wraps a `reqwest::Client` with
// `redirect(Policy::none())` + a 10s timeout and the `APIKeyManager` for secret
// decryption (plaintext fallback). `fire`/`_deliver` are raw SQL over the
// `webhooks` table via `core::database::session_local()` (the ORM models in
// `src/database.rs` are not ported). HMAC-SHA256 over the JSON body via
// `hmac`+`sha2`+`hex`. DOCUMENTED DRIFT: the BODY timestamp is
// `datetime.utcnow().isoformat()` (`T` separator, microseconds only when
// non-zero) — distinct from the space-separated SQLite DateTime row format used
// for `last_triggered_at`. `fire_and_forget`'s sync-thread
// `run_coroutine_threadsafe` fallback collapses to a single tokio `Handle` spawn.
pub mod webhook_manager;
// `caldav_sync` <- src/caldav_sync.py — CalDAV -> local SQLite one-way pull.
// PORT_PARTIAL (web). No Rust `caldav` crate, so the protocol is hand-rolled over
// `reqwest` (blocking, inside `spawn_blocking` — the `asyncio.to_thread`
// analogue) with custom HTTP methods (`PROPFIND` / `REPORT`), Depth headers, and
// small XML bodies (the mcp_manager / core::codex hand-rolled-protocol
// precedent). VEVENT parsing uses the `icalendar` crate (`DatePerhapsTime` -> the
// `_to_utc_naive` Date/Aware/Naive branch model; `WithTimezone` -> UTC via
// `chrono_tz` since icalendar's `chrono-tz` feature is off). DB upsert/prune is
// raw SQL over `calendars` (source='caldav', color='#5b8abf') + `calendar_events`
// (by uid; in-window stale prune). HONEST DEFERS: `routes.prefs_routes._load_for_user`
// is unported -> a re-implemented `data/user_prefs.json` (CWD-relative, `_users`/
// flat shapes) loader so creds CAN be read if present; unconfigured -> the honest
// `errors: ["CalDAV is not configured"]` shape (never a fake sync). Multi-calendar
// PROPFIND discovery (current-user-principal -> calendar-home-set -> Depth:1 list)
// is best-effort with a single-calendar-URL fallback.
pub mod caldav_sync;
// `caldav_writeback` <- src/caldav_writeback.py — push local calendar edits back
// to the remote CalDAV server (build_event_ical / find_remote_calendar /
// push_event / writeback_event).
pub mod caldav_writeback;
// `task_endpoint` <- src/task_endpoint.py — the shared background-task endpoint
// resolver (`resolve_task_endpoint`). PORT_PARTIAL (web). Composes the same
// settings cascade `builtin_actions` uses (settings::get_setting ->
// model_endpoints::endpoint_auth -> endpoint_resolver builders); the per-user
// `settings.get_user_setting` override layer is the documented honest defer
// (admin settings are read instead). Consumed by `session_actions::run_auto_sort`.
pub mod task_endpoint;
// `event_bus` <- src/event_bus.py — the automation event bus. PORT_VIA_DB (web).
// `set/get_task_scheduler` over a `Lazy<RwLock<Option<Arc<TaskScheduler>>>>`
// global; `fire_event` spawns `_handle_event` on the current tokio runtime
// (`Handle::try_current`) or, with no runtime, on a fresh current-thread one (the
// `asyncio.run` fallback). `_handle_event` is raw SQL over `scheduled_tasks`
// (trigger_type='event' AND trigger_event=? AND status='active' AND (owner=? |
// owner IS NULL)), bumps `trigger_counter`, and on threshold sets next_run=now +
// `run_task_now(id, false)`. `_resolve_event_owner` reads
// `src::constants::DATA_DIR/auth.json` (first is_admin, else first user). DEVIATION:
// the DB commits happen before the connection is dropped, then tasks fire —
// `rusqlite::Connection` is not `Send`, so it is not held across the
// `run_task_now().await`; the commit-then-fire order is preserved.
pub mod event_bus;
// `cleanup_service` <- src/cleanup_service.py — session archival + deletion.
// PORT_VIA_DB (web). `CleanupConfig` consts (7/14/20/10, PROTECTED_KEYWORDS,
// 512); `archive_inactive_sessions` / `cleanup_old_sessions` /
// `get_cleanup_preview` / `cleanup_sessions` are raw SQL over `sessions` +
// `chat_messages` via `core::database::session_local()`. recent_session_ids =
// first 10 by created_at DESC; preview dicts via serde_json Map (preserve_order),
// round-2-dp, isoformat-or-"Unknown". `cleanup_old_sessions`/`cleanup_sessions`
// take the `&SessionManager` and, after the bulk `DELETE FROM sessions`, evict
// each deleted id from the in-memory `sessions` cache via
// `SessionManager::evict_cached_sessions` (cache-only) — mirroring the Python
// `del session_manager.sessions[id]` so a deleted-but-cached session is not
// served by `get_session` until restart.
pub mod cleanup_service;
// `session_actions` <- src/session_actions.py — reusable session cleanup +
// AI folder-sort. PORT_VIA_DB (web). `run_auto_sort(owner, skip_llm)` Phase 1
// deletes empty/throwaway sessions (raw SQL on `sessions` + `chat_messages`;
// `_THROWAWAY_NAMES` set, `_THROWAWAY_MAX_MESSAGES=4`); Phase 2 resolves the
// endpoint via `task_endpoint::resolve_task_endpoint(None,None,None)` +
// `llm_call_async` (16384 tokens) + the 3-tier JSON extract (serde_json ->
// ```json fence regex -> brace slice) + the 8-char-id-prefix folder apply. Wires
// into `builtin_actions::action_tidy_sessions` (skip_llm=true, 60s timeout).
pub mod session_actions;
// `document_actions` <- src/document_actions.py — reusable document tidy.
// PORT_VIA_DB (web). `run_document_tidy(owner)` removes junk docs (empty /
// placeholder / throwaway-title / email-quote-chain) and dedups survivors by
// (normalized title, content fingerprint), keeping the most complete copy. Raw
// SQL over `documents`; `_JUNK_TITLES` set + the `_norm_title` /
// `_content_fingerprint` / `_real_len` regex helpers. `raise TaskNoop` ->
// `Err(builtin_actions::Outcome::noop(...))`. Wires into
// `builtin_actions::action_tidy_documents`.
pub mod document_actions;
