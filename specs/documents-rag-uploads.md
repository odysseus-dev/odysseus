# Documents, RAG, And Uploads

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers file/document context, document storage, and vector retrieval in:

- `app.py` and `src/app_initializer.py` route/manager wiring;
- `routes/upload_routes.py`, `routes/personal_routes.py`, `routes/embedding_routes.py`, `routes/document_routes.py`, and `routes/document_helpers.py`;
- chat attachment paths in `routes/chat_routes.py`, `routes/chat_helpers.py`, `src/chat_handler.py`, and `src/chat_processor.py`;
- `src/upload_handler.py` and `src/upload_limits.py`;
- `src/document_processor.py`, `src/document_actions.py`, `src/personal_docs.py`, and `src/markitdown_runtime.py`;
- `src/rag_singleton.py`, `src/rag_vector.py`, `src/rag_manager.py`, `src/chroma_client.py`, `src/embeddings.py`, and `src/embedding_lanes.py`;
- PDF/form helpers in `src/pdf_runtime.py`, `src/pdf_forms.py`, and `src/pdf_form_doc.py`;
- `services/docs/service.py`;
- document, upload, RAG, chat, email, and admin frontend callers in `static/app.js`, `static/js/chat.js`, `static/js/chatRenderer.js`, `static/js/fileHandler.js`, `static/js/document.js`, `static/js/documentLibrary.js`, `static/js/rag.js`, `static/js/admin.js`, `static/js/emailInbox.js`, and `static/js/slashCommands.js`;
- tests covering upload, document, attachment, PDF, RAG, Chroma, MarkItDown, and embedding behavior.

## Runtime Integration

`app.py` registers upload, personal-doc/RAG, embedding, document, diagnostics, and Codex document routes. `src.app_initializer.initialize_managers()` creates `UploadHandler` and `PersonalDocsManager`, and startup attempts to initialize the RAG singleton.

`src.rag_singleton.get_rag_manager()` returns the live `VectorRAG` instance when Chroma/embedding dependencies are reachable. Personal routes can retry the singleton and return explicit 503s when unavailable. Chat RAG uses the `PersonalDocsManager.rag_manager` captured during app initialization and can silently skip RAG if that manager is absent.

## Uploads And Attachments

`src.upload_handler.UploadHandler` owns upload IDs, safe filenames, upload metadata, atomic `uploads.json` writes, and file storage under `data/uploads`.

`routes/upload_routes.py` owns:

- `POST /api/upload`, returning uploaded file metadata;
- admin upload cleanup and stats;
- `GET /api/upload/{file_id}`;
- `GET/PUT /api/upload/{file_id}/vision` for editable OCR/vision cache;
- thumbnail and masked owner/admin access behavior.

It does not currently expose a general upload list/delete route.

Readable/code-like upload handling includes common text/code extensions plus `.nix`; document processing renders recognized code-like text into fenced blocks with language metadata.

Chat does not own attachment extraction. Runtime flow:

- the frontend uploads files and submits attachment IDs;
- `ChatHandler.preprocess_message()` resolves IDs with the session owner through `UploadHandler.resolve_upload()`;
- vision/OCR cache and attachment metadata are prepared before model calls;
- text-only models receive stripped multimodal blocks;
- `src.document_processor.build_user_content()` produces model-ready text, PDF text, Office/EPUB text when MarkItDown is available, image/multimodal blocks, truncation, and PDF auto-document updates;
- chat streams attachment, PDF-created `doc_update`, and `rag_sources` events where applicable.

## Living Documents And PDF

`routes/document_routes.py` owns the HTTP document API: create/read/update/archive/delete, library listing, import/export, version history, tidy/AI tidy, PDF rendering/export, PDF form helpers, and email-attachment reply preparation.

`static/js/documentLibrary.js` owns local library state after archive/delete actions, including total counts and language chips. Server route truth still owns durable document state.

`static/js/document.js` owns the browser document editor and markdown preview. Preview rendering applies code highlighting when highlight.js is present and renders Mermaid diagrams when the Mermaid runtime is available.

Document mutations also happen through agent tools, Codex document routes, email attachment import, and scripts. Those callers must preserve document owner and version semantics.

`Document` rows own current content and owner. `DocumentVersion` rows own immutable snapshots. Document access should be owner-filtered, not session-id-only; the session document listing path still needs regression coverage for per-document owner filtering after the session owner check.

PDF runtime behavior:

- direct PDF import stores the upload through `UploadHandler`;
- pypdf text extraction remains core;
- PyMuPDF enables form detection, page rendering, page PNGs, annotation fill, render/export PDF, and form filling;
- imported PDFs become either plain `pdf_source` markdown or `pdf_form_source` markdown with sidecar field data;
- PDF markers must resolve back through an upload owned by the caller;
- signed-reply preparation uses document `source_email_*` provenance and verifies the document owner and signature owner. Source email account resolution still needs explicit owner-scoped coverage.

## Personal Docs And RAG

`src.personal_docs.PersonalDocsManager` owns personal-directory indexing and keyword retrieval.

`src.rag_vector.VectorRAG` owns Chroma/embedding-backed indexing and owner-filtered retrieval. Chunk ids are owner-scoped so byte-identical chunks from different owners do not suppress each other. `src.rag_singleton` owns lazy initialization, retry throttling, and reset behavior.

`routes/personal_routes.py` owns personal-doc and direct RAG-upload routes. Directory list/index/delete routes are admin-gated. Direct RAG upload is currently user-authenticated but not admin-gated, writes to `data/personal_uploads`, and has looser file-type validation than normal uploads.

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

`services/docs/service.py` is a separate facade and currently has result-shape drift from `VectorRAG`: it maps legacy `text`/`content` and `indexed`/`failed` keys while the live vector path returns `document`/`similarity` and `indexed_count`/`failed_count`.

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
- PDF marker ownership checks before resolving source uploads;
- personal-directory confinement helpers;
- owner-filtered `VectorRAG.search(owner=...)`;
- shared untrusted-context wrappers for RAG preface insertion.

Extracted attachment text is currently appended into the user message rather than wrapped as a separate untrusted-context message. That is current behavior and a prompt-injection hardening gap.

Bearer-token callers are not a scoped document/upload API surface today. Routes that treat token-authenticated users as owners need explicit scope/effective-user policy before they are considered safe token APIs.

## Testing Coverage

Existing useful coverage includes upload owner scope, upload IDs, upload atomicity, attachment budgets, `.nix` text upload handling, upload/PDF security regressions, RAG owner fallback, Chroma fast-fail, MarkItDown runtime, PDF runtime, document-library counter updates, and selected document helper behavior.

Route-level coverage is thinner for document CRUD, PDF import/render/export/fill, direct RAG upload, embedding admin/security behavior, and RAG unavailable states.

## Current Gaps

- Direct RAG upload needs clear auth, file-type validation, and MarkItDown/PDF extraction parity decisions.
- Document `session_id` relinking and session document listing need owner-scope regressions.
- `services/docs/service.py` return-shape mapping is stale relative to `VectorRAG`.
- Chat RAG can remain degraded after startup even if personal routes later initialize the RAG singleton.
- PyMuPDF-dependent routes do not all share the same optional-runtime helper/error behavior.
- Signed-reply preparation needs owner-scoped source email account/signature regression coverage.
- Document/upload routes need explicit bearer-token scope/effective-user policy.
- User-facing document/PDF/RAG route matrices need more regression coverage for owner denial, admin gates, unavailable services, and degraded optional dependencies.
