# Documents, RAG, And Uploads

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers file/document context, document storage, and vector retrieval in:

- `app.py` and `src/app_initializer.py` route/manager wiring;
- `routes/upload_routes.py`, `routes/personal_routes.py`, `routes/embedding_routes.py`, canonical `routes/document/document_routes.py` and `routes/document/document_helpers.py`, plus their top-level compatibility shims;
- chat attachment paths in `routes/chat_routes.py`, `routes/chat_helpers.py`, `src/chat_handler.py`, and `src/chat_processor.py`;
- `core/session_manager.py`, `src/attachment_refs.py`, `src/upload_handler.py`,
  `src/upload_limits.py`, and the public reference contract in
  `docs/attachments.md`;
- `src/document_processor.py`, `src/document_actions.py`, `src/personal_docs.py`, and `src/markitdown_runtime.py`;
- `src/rag_singleton.py`, `src/rag_vector.py`, `src/rag_manager.py`, `src/chroma_client.py`, `src/embeddings.py`, and `src/embedding_lanes.py`;
- PDF/form helpers in `src/pdf_runtime.py`, `src/pdf_forms.py`, and `src/pdf_form_doc.py`;
- `services/docs/service.py`;
- document, upload, RAG, chat, email, and admin frontend callers in `static/app.js`, `static/js/chat.js`, `static/js/chatRenderer.js`, `static/js/fileHandler.js`, `static/js/document.js`, `static/js/documentLibrary.js`, `static/js/rag.js`, `static/js/admin.js`, `static/js/emailInbox.js`, and `static/js/slashCommands.js`;
- tests covering upload, document, attachment, PDF, RAG, Chroma, MarkItDown, and embedding behavior.

## Runtime Integration

`app.py` registers upload, personal-doc/RAG, embedding, document, diagnostics, and Codex document routes. `src.app_initializer.initialize_managers()` creates `UploadHandler` and `PersonalDocsManager`, installs the upload handler on `SessionManager` and the shared tool helper, and startup attempts to initialize the RAG singleton. App route wiring passes that same handler to session/history, document, note, and calendar writers that can persist upload references.

`src.rag_singleton.get_rag_manager()` returns the live `VectorRAG` instance when Chroma/embedding dependencies are reachable. Personal routes can retry the singleton and return explicit 503s when unavailable. Chat RAG uses the `PersonalDocsManager.rag_manager` captured during app initialization and can silently skip RAG if that manager is absent.

## Uploads And Attachments

`src.upload_handler.UploadHandler` owns upload IDs, safe filenames, upload metadata, owner rename rewrites, atomic `uploads.json` writes, content-type detection, and file storage under `data/uploads`. Upload IDs accept extensionless values or one sanitized alphanumeric extension.

Upload-index reads track the live and `.bak` files by device, inode, size, nanosecond mtime, and ctime, then verify the combined signature after parsing. This catches same-timestamp corruption/replacement and prevents stale parsed data from being cached under a newer file identity. Non-destructive reads can recover from the backup; destructive cleanup requires a valid live index and never treats an older backup as deletion authority. Lifecycle writes can synchronize the backup so intentionally removed metadata is not resurrected.

`src.upload_limits` owns central upload-size caps and environment overrides for chat attachments, gallery, transforms, memory import, personal uploads, email compose, STT audio, and ICS imports. Invalid configured limits fail fast at import so routes do not silently accept unsafe sizes. Docker installs `libmagic1` plus `python-magic` so `UploadHandler.detect_content_type()` can sniff bytes in the official image; native installs can fall back to extension/MIME guesses when `python-magic` is unavailable.

`routes/upload_routes.py` owns:

- `POST /api/upload`, returning uploaded file metadata;
- reference-aware admin upload cleanup and stats;
- `GET /api/upload/{file_id}`;
- `GET/PUT /api/upload/{file_id}/vision` for editable OCR/vision cache;
- thumbnail and masked owner/admin access behavior.

It does not currently expose a general upload list/delete route. Download/preview responses that serve uploaded content should include `X-Content-Type-Options: nosniff` where route code owns the response so browser MIME sniffing does not widen accepted upload types.

Readable/code-like upload handling includes common text/code extensions plus `.nix`; document processing renders recognized code-like text into fenced blocks with language metadata.

Chat does not own attachment extraction. Runtime flow:

- the frontend uploads files and submits attachment IDs;
- `ChatHandler.preprocess_message()` resolves IDs with the session owner through `UploadHandler.resolve_upload()`, which enforces owner/admin access and no longer treats missing owner context as permission to read owned uploads;
- vision/OCR cache and attachment metadata are prepared before model calls;
- text-only models receive stripped multimodal blocks;
- `src.document_processor.build_user_content()` produces model-ready text, PDF text, Office/EPUB text when MarkItDown or the DOCX fallback is available, image/multimodal blocks, truncation, and PDF/Office auto-document updates;
- chat streams attachment, PDF-created `doc_update`, and `rag_sources` events where applicable.

Extensionless image and audio attachments derive their data-URI subtype from
the detected MIME type, so `image/png` and `audio/mpeg` uploads do not become
invalid `data:image/;base64` or `data:audio/;base64` blocks when the filename
has no extension.

## Durable References And Cleanup

`src.attachment_refs` owns the stable `attachment_ref` shape used outside raw
upload storage: attachment id, name, MIME type, size, and optional checksum,
creation time, dimensions, vision text/model, and gallery id. Live provider
calls may still receive multimodal data URLs for the current turn, but durable
chat content is normalized to readable text plus compact reference lines.
Structured references remain in message attachment metadata, and chat FTS
triggers omit inline media while startup migration scrubs legacy indexed data
URLs.

Agent/tool manifests expose `odysseus://attachment/<id>` with
`read_policy: "owner_checked_upload"`. A compatibility filesystem path is
included only after owner-aware upload resolution, upload-root confinement, and
tool-readable-root checks; the stable contract for external tools is the URI
and attachment id, not host layout.

Writers reserve referenced uploads before committing durable state. This
includes session message append/replace and history rewrites, document
create/update and native document edits, note route/tool create/update,
calendar/event route/tool create/update, and attachment-bearing session
updates. A missing or wrong-owner reference aborts before destructive
replacement and surfaces a route conflict or tool error. Reservations serialize
with cleanup through the upload-index lock and refresh access time.

Admin cleanup first scans chat content and attachment metadata, current and
versioned documents including PDF markers, gallery filenames/hashes, note
image/color/content/checklist fields, and calendar color/description/location
fields. Reference discovery or index-integrity failure aborts cleanup; the
lower-level API removes nothing without both completed id and hash snapshots.
Only expired, unreferenced files with coherent id/path/owner/checksum/timestamp
metadata are candidates. Matching index rows are persisted away before byte
deletion and restored if deletion fails. This lock is process-local, so the
documented race protection assumes the current single-worker deployment.

## Living Documents And PDF

`routes/document/document_routes.py` owns the HTTP document API: create/read/update/archive/delete, library listing, import/export, version history, tidy/AI tidy, PDF rendering/export, PDF form helpers, and email-attachment reply preparation. The top-level document route/helper modules remain compatibility aliases.

`static/js/documentLibrary.js` owns local library state after archive/delete actions, including total counts and language chips. Server route truth still owns durable document state.

`static/js/document.js` owns the browser document editor and markdown preview. Preview rendering applies code highlighting when highlight.js is present, renders Mermaid diagrams when the Mermaid runtime is available, refreshes after AI edits, and discards pending AI diffs before switching the active document.

Document mutations also happen through agent tools, Codex document routes, email attachment import, and scripts. HTTP and native-agent document writers owner-reserve any internal upload/PDF references before persisting new current content or versions. Native document tool outputs include metadata that the browser can use to open/update the editor if a later stream update is missed. Those callers must preserve document owner, attachment, and version semantics.

After external/workspace-untrusted context, a proposed document mutation is sealed into an exact approval with document id, current version, content digest, tool content, owner/session, and workspace. Approval continuation re-reads and verifies those fields before consuming the one-use authorization, so an intervening edit cannot apply a stale approved patch to new content.

Email draft documents are a first-class document language. Create/update paths
detect the `To`/`Subject`/header shape, coerce language to `email`, and preserve
protected reply/forward headers such as `In-Reply-To`, `References`,
`X-Source-UID`, `X-Source-Folder`, attachment headers, and quoted/original
history when model or UI edits replace the draft body. Creating a draft for the
same source UID/folder in the same session updates the active draft instead of
creating a duplicate.

`Document` rows own current content and owner. `DocumentVersion` rows own immutable snapshots. Document access should be owner-filtered, not session-id-only; the session document listing path still needs regression coverage for per-document owner filtering after the session owner check.

PDF runtime behavior:

- direct PDF import stores the upload through `UploadHandler`;
- PDF library entries preserve metadata/preview behavior for source PDFs;
- pypdf text extraction remains core;
- PyMuPDF enables form detection, page rendering, page PNGs, annotation fill, render/export PDF, and form filling;
- PDF render routes should return a shaped 503 when PyMuPDF is absent and use same-origin framing/download behavior for rendered pages;
- imported PDFs become either plain `pdf_source` markdown or `pdf_form_source` markdown with sidecar field data;
- PDF markers must resolve back through an upload owned by the caller;
- signed-reply preparation uses document `source_email_*` provenance and verifies the document owner and signature owner. Source email account resolution still needs explicit owner-scoped coverage.

Office/EPUB attachment extraction is optional and MarkItDown-backed for `.docx`, `.pptx`, `.xlsx`, `.xls`, and `.epub`; a pure-Python DOCX fallback can extract `word/document.xml`. When a session id is present, full extraction can be saved as a markdown `Document` while the chat-inline copy remains capped.

## Personal Docs And RAG

`src.personal_docs.PersonalDocsManager` owns personal-directory indexing and keyword retrieval.

`src.rag_vector.VectorRAG` owns Chroma/embedding-backed indexing and owner-filtered retrieval. Chunk ids are owner-scoped so byte-identical chunks from different owners do not suppress each other. `src.rag_singleton` owns lazy initialization, retry throttling, and reset behavior.

`routes/personal_routes.py` owns personal-doc and direct RAG-upload routes. Directory list/index/delete routes are admin-gated, and directory indexing runs in a worker thread so traversal/extraction does not block the async event loop. Direct RAG upload is user-authenticated, requires document privilege, forwards owner into the manager wrapper, writes unique files under per-owner subdirectories of `data/personal_uploads`, and has looser file-type validation than normal uploads.

Current call sites include:

- admin RAG pages and slash commands;
- chat RAG preface building;
- AI interaction and MCP RAG management tools;
- CLI scripts for document/personal indexing.

Some non-route tool/script paths can index ownerless or arbitrary directories and should be treated as compatibility-sensitive management surfaces.

## Embedding Models

`routes/embedding_routes.py` owns admin-gated embedding model and custom endpoint management. It validates custom endpoints with outbound URL checks, can persist and process-expose `EMBEDDING_API_KEY`, resets embedding/RAG/tool-index/Chroma state, and does not own document extraction.

`src.embeddings` owns HTTP embedding fallback to FastEmbed and process-level endpoint state. `src.embedding_lanes` keeps custom HTTP embedding vectors separate from FastEmbed fallback vectors with lane-specific Chroma collections, migrates legacy unsuffixed collections into empty lanes, and dedupes query results across lanes. `src.chroma_client` owns native Chroma defaults and fast reachability checks.

## Compatibility State

`src.rag_manager.RAGManager` is a backward-compat wrapper. The live owner-aware vector path is `VectorRAG`.

`services/docs/service.py` is a separate facade. It accepts live `VectorRAG` query rows (`document`, `similarity`, nested metadata source), retains legacy `text`/`content` and `score` fallbacks, skips non-object rows, and maps live `indexed_count`/`failed_count` plus legacy `indexed`/`failed` index summaries into its dataclasses.

`src.database` re-exports `core.database`; document models and migrations live in `core.database`.

## Optional And Degraded Behavior

- ChromaDB/FastEmbed are default installed dependencies, but Chroma can be offline or unreachable.
- Native Chroma defaults to `localhost:8100`; Docker uses the `chromadb:8000` compose service and persistent Chroma storage.
- HTTP embeddings can fall back to FastEmbed; when both lanes exist, lane separation avoids Chroma dimension conflicts.
- MarkItDown is optional for Office/EPUB extraction; chat attachments and personal directory indexing have clear degraded behavior, while direct RAG upload does not share the same extraction path.
- PyMuPDF is optional, unlocks PDF form/render/fill paths, and carries AGPL implications when installed.
- PyMuPDF-dependent document routes should use the shared runtime helper/error text so missing-dependency and license policy stay visible.
- pypdf text extraction is core and should remain available without PyMuPDF.

## Security And Provenance

Uploaded files, documents, RAG chunks, extracted attachment text, OCR/vision text, PDF marker content, and source-email metadata are untrusted external or user-provided context when sent to an LLM.

Concrete enforcement points include:

- `UploadHandler.resolve_upload()` for upload ID validation, owner/admin access, and upload-dir confinement;
- owner-checked write reservations before durable attachment references are
  stored, sharing the upload-index lock with reference-aware cleanup;
- PDF marker ownership checks before resolving source uploads;
- personal-directory and personal-upload confinement helpers, including symlink/realpath checks before deleting uploaded files or removing indexed directories;
- owner-filtered `VectorRAG.search(owner=...)`;
- shared untrusted-context wrappers for RAG preface insertion.

Extracted attachment text is currently appended into the user message rather than wrapped as a separate untrusted-context message. That is current behavior and a prompt-injection hardening gap.

Bearer-token callers are not a scoped document/upload API surface today. Routes that treat token-authenticated users as owners need explicit scope/effective-user policy before they are considered safe token APIs.

## Testing Coverage

Existing useful coverage includes upload owner scope, upload IDs, upload atomicity, durable attachment reference normalization, message/document/note/calendar write reservations, fail-closed reference-aware cleanup, attachment budgets, `.nix` text upload handling, upload/PDF security regressions, Docker `libmagic`/`python-magic` upload detection, RAG owner fallback, Chroma fast-fail, MarkItDown runtime, PDF runtime, document-library counter updates, and selected document helper behavior.

Route-level coverage is thinner for document CRUD, PDF import/render/export/fill, direct RAG upload, embedding admin/security behavior, and RAG unavailable states.

## Current Gaps

- Direct RAG upload still needs clearer file-type validation and MarkItDown/PDF extraction parity decisions.
- Document `session_id` relinking and session document listing need owner-scope regressions.
- Chat RAG can remain degraded after startup even if personal routes later initialize the RAG singleton.
- PyMuPDF-dependent routes do not all share the same optional-runtime helper/error behavior.
- Signed-reply preparation needs owner-scoped source email account/signature regression coverage.
- Document/upload routes need explicit bearer-token scope/effective-user policy.
- User-facing document/PDF/RAG route matrices need more regression coverage for owner denial, admin gates, unavailable services, and degraded optional dependencies.
