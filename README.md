# Odysseus Desktop

Windows-first desktop MVP for local Odysseus chat, documents, RAG, optional OCR,
and non-destructive legacy import.

This app is a Tauri desktop shell with a Rust supervisor, a bundled Python
JSON-RPC sidecar over stdio, profile-local SQLite storage, React/TypeScript UI,
and local model runtime detection for Ollama at `127.0.0.1:11434`.

No Docker is required. No app-internal service binds to `0.0.0.0`. Localhost is
used only for external local runtimes such as Ollama and the Vite dev server.

## MVP Scope

Included:

- Basic non-RAG chat.
- Sessions, settings, default profile, and restart persistence.
- `.txt`, `.md`, and extractable `.pdf` document import.
- SQLite + NumPy VectorStore-backed RAG.
- Embedding cache by chunk/content hash.
- Optional OCR for scanned/low-text PDFs when Tesseract plus `pdftoppm` or
  `mutool` are locally installed.
- Non-destructive import from compatible legacy Odysseus data folders.
- Profile-local logs and app data.

Excluded from the MVP:

- Tools, agents, email, calendar, shell, Cookbook, gallery/editor, full MCP,
  Chroma, Docker, and hidden HTTP.

## User Setup

Install the Windows build from:

```powershell
src-tauri\target\release\bundle\nsis\Odysseus Desktop_0.1.0_x64-setup.exe
```

The installer includes the app, Python sidecar code, and a bundled embedded
Python runtime with required Python dependencies. A system Python install is not
required to run the installed app.

For local chat, install and run Ollama separately:

```powershell
ollama serve
ollama pull llama3.2
```

OCR is optional. To OCR scanned PDFs, install Tesseract and at least one PDF
renderer:

- Tesseract OCR
- MuPDF `mutool`, or Poppler `pdftoppm`

If OCR tools are missing, scanned PDFs remain listed with a clear OCR-unavailable
state. Normal text, Markdown, extractable PDF import, and non-RAG chat still work.

## Data and Logs

User data is stored in the app-data profile folder:

```powershell
%APPDATA%\dev.odysseus.desktop\profiles\default
```

Important files:

- `app.db` - profile-local SQLite database.
- `files\documents` - profile-local copies of imported documents.
- `logs\backend.log` - Python sidecar startup, JSON-RPC errors, Ollama detection,
  document import, OCR, and legacy import logs.

Quit and relaunch should preserve sessions, settings, documents, chunks,
embeddings, OCR pages, and RAG behavior.

## Developer Setup

Install prerequisites:

- Node.js
- Rust and Cargo
- Windows WebView2 runtime
- Optional: Ollama for real chat/RAG smoke testing
- Optional: Tesseract plus `mutool` or `pdftoppm` for real OCR smoke testing

Install Node dependencies:

```powershell
npm install
```

Run backend tests:

```powershell
python -m pip install -r python\requirements.txt
python -m pytest python/tests
```

Run the frontend build:

```powershell
npm run build
```

Run the desktop app in development:

```powershell
npm run tauri:dev
```

`tauri:dev` stages the embedded Python runtime first, then starts Tauri. The
development UI uses Vite on `127.0.0.1:1420`.

## Packaging

Stage and verify the embedded Python runtime:

```powershell
npm run prepare:python
npm run verify:python
```

The staged runtime lives in `python-runtime` and is bundled as a Tauri resource.
It must import:

- `json`
- `sqlite3`
- `numpy`
- `pypdf`
- `rpc_server`
- `odysseus_desktop_backend`

Build the Windows installer:

```powershell
npm run tauri:build
```

The NSIS installer is emitted under:

```powershell
src-tauri\target\release\bundle\nsis
```

Run the smoke gate:

```powershell
npm run smoke
```

To skip re-downloading/revalidating the embedded runtime during repeated local
checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\smoke-mvp.ps1 -SkipRuntime
```

The smoke gate covers fresh profile startup, Ollama missing/reachable detection,
basic chat, document import, RAG chat/retrieval, OCR unavailable/available mocked
paths, legacy import reporting, restart persistence, frontend build, and Rust
`cargo check`.

## License and Notices

Odysseus Desktop preserves the upstream Odysseus MIT license in `LICENSE`.
Third-party and optional OCR notices are tracked in `THIRD_PARTY_NOTICES.md`.
