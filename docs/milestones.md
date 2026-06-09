# Milestone Gates

## Milestone 1: Desktop Foundation

Acceptance:

- The desktop app creates a default local profile on first launch.
- Rust starts and supervises the bundled Python sidecar.
- React talks to Rust, and Rust talks to Python using JSON-RPC over stdio.
- Settings, sessions, messages, and runtime status persist in SQLite.
- Ollama detection probes only `127.0.0.1:11434`.
- Basic chat sends one prompt to Ollama and stores the user and assistant messages.
- Closing the app sends `app.shutdown` and terminates the sidecar cleanly.
- Relaunching restores settings, sessions, and messages.

Milestone 1 must not include:

- document context
- RAG
- memory retrieval
- tools
- agents
- advanced routing
- email
- calendar
- shell tools
- Cookbook
- gallery/editor
- full MCP

## Milestone 2: Documents And RAG

Start only after Milestone 1 passes.

- Add document import, chunking, embeddings, retrieval, deletion, and reindexing.
- All vector behavior goes through a `VectorStore` abstraction.
- MVP `VectorStore` uses SQLite + NumPy.
- Embeddings are cached by chunk/content hash so unchanged chunks are not
  re-embedded.
- LanceDB or sqlite-vec must be swappable later without rewriting RAG callers.
- OCR is not implemented in Milestone 2. Low-text/scanned PDFs are marked with
  `index_status = low_text` and surfaced in the UI as Milestone 3 work.
- Chat RAG is explicit per request. Default chat remains the Milestone 1
  non-RAG path.

## Milestone 3: OCR And Migration

Start only after Milestone 2 passes.

- Detect low-text/scanned files.
- Offer optional OCR only when an engine is available.
- Store OCR text with page/source metadata and confidence where available.
- Import compatible existing Odysseus data non-destructively.
- OCR uses detected local tooling only. MVP detection looks for Tesseract plus
  a PDF renderer (`pdftoppm` or `mutool`).
- OCR output replaces the document page text and flows through the existing
  RAG path; there is no separate OCR index.
- Legacy import reads old folders only, copies compatible data into the active
  profile, and reports skipped/incompatible/failed items.
