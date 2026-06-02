# Acknowledgments

Odysseus stands on the shoulders of a lot of open-source work. This file
credits the projects whose code, assets, or designs are included in or
adapted by this repository, and notes their licenses.

If you believe something here is mis-attributed or missing, please open an
issue — it will be corrected promptly.

---

## Adapted / borrowed code

Portions of this project were adapted from other open-source repositories.
Their original authors retain copyright over the adapted portions, under the
licenses noted below.

The sources below are under permissive licenses (MIT / Apache-2.0), which permit
this use as long as their original copyright and license notices are preserved.
The full license texts are kept in [`licenses/`](licenses/).

- **[opencode](https://github.com/anomalyco/opencode)** — open-source AI coding
  agent (originally [opencode-ai/opencode](https://github.com/opencode-ai/opencode),
  archived Sep 2025; now maintained at `anomalyco/opencode`). Copyright © the
  opencode authors. **MIT License.** Adapted for agent-loop / tool-execution
  patterns and UI concepts.
- **[llmfit](https://github.com/AlexsJones/llmfit)** by **Alex Jones** — the
  engine behind the Cookbook's model download / serve / "What Fits?" feature.
  Copyright © Alex Jones. **MIT License.** Adapted in `services/hwfit/`
  (hardware detection, quant-aware fit scoring, model catalog),
  `routes/cookbook_*.py`, `routes/hwfit_routes.py`, `static/js/cookbook*.js`,
  and `scripts/odysseus-cookbook`.
- **[Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch)** by
  **Alibaba-NLP / Tongyi Lab** — the multi-step deep-research agent pipeline.
  Copyright © Alibaba-NLP / Tongyi Lab. **Apache-2.0.** Adapted for Odysseus's
  Deep Research feature (`api/research_*.py`, `routes/research_routes.py`,
  `services/search/`). Full text in
  [`licenses/DeepResearch-Apache-2.0.txt`](licenses/DeepResearch-Apache-2.0.txt).

---

## Bundled via Docker Compose

These services are pulled as images by the project's `docker-compose.yml`
and run alongside Odysseus on `docker compose up`. They are not modified —
just composed.

| Service | Image | Purpose | License |
|---|---|---|---|
| [SearXNG](https://github.com/searxng/searxng) | `searxng/searxng:latest` | Default metasearch backend | AGPL-3.0 |
| [ChromaDB](https://github.com/chroma-core/chroma) | `chromadb/chroma:latest` | Vector store for memory / RAG | Apache-2.0 |
| [ntfy](https://github.com/binwiederhier/ntfy) | `binwiederhier/ntfy` | Push notifications (self-hosted reminders) | Apache-2.0 / GPL-2.0 |

## Bundled front-end libraries

Vendored in `static/lib/` and served directly:

| Library | Purpose | License |
|---|---|---|
| [highlight.js](https://github.com/highlightjs/highlight.js) v11.9.0 | Code syntax highlighting | BSD-3-Clause |
| [SheetJS / xlsx](https://github.com/SheetJS/sheetjs) (`xlsx.full.min.js`) | Spreadsheet (`.xlsx`) read/write | Apache-2.0 |
| [docx](https://github.com/dolanmiu/docx) (`docx.umd.min.js`) | Generate `.docx` documents | MIT |
| [mammoth.js](https://github.com/mwilliamson/mammoth.js) | Convert `.docx` → HTML | BSD-2-Clause |
| [html2pdf.js](https://github.com/eKoopmans/html2pdf.js) | HTML → PDF export (bundles jsPDF + html2canvas) | MIT |
| [jsPDF](https://github.com/parallax/jsPDF) (bundled in html2pdf) | PDF generation | MIT |
| [html2canvas](https://github.com/niklasvh/html2canvas) (bundled in html2pdf) | DOM → canvas rasterization | MIT |
| [node-qrcode](https://github.com/soldair/node-qrcode) (`qrcode.min.js`) | QR-code rendering (2FA setup) | MIT |

## Front-end libraries loaded at runtime (CDN)

Referenced from `cdn.jsdelivr.net` / `cdnjs.cloudflare.com` at runtime — not vendored:

| Library | Purpose | License |
|---|---|---|
| [KaTeX](https://github.com/KaTeX/KaTeX) 0.16.22 | Math typesetting | MIT |
| [Mermaid](https://github.com/mermaid-js/mermaid) 11 | Diagrams from text | MIT |
| [Pyodide](https://github.com/pyodide/pyodide) 0.27.5 | In-browser Python runtime | MPL-2.0 |
| [PDFObject](https://github.com/pipwerks/PDFObject) 2.1.1 | Inline PDF embedding | MIT |

## Fonts

Bundled in `static/fonts/`:

| Font | License | Author |
|---|---|---|
| [Fira Code](https://github.com/tonsky/FiraCode) | SIL Open Font License 1.1 | Nikita Prokopov & contributors |
| [Inter](https://github.com/rsms/inter) | SIL Open Font License 1.1 | Rasmus Andersson |
| [GohuFont](https://font.gohu.org/) (`fonts/custom/GohuFont.ttf`) | WTFPL | Hugo Chargois |

## Python dependencies

Core (`requirements.txt`) and optional (`requirements-optional.txt`):

| Package | License |
|---|---|
| FastAPI | MIT |
| Uvicorn | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| python-dotenv | BSD-3-Clause |
| HTTPX | BSD-3-Clause |
| Pydantic / pydantic-settings | MIT |
| SQLAlchemy | MIT |
| pypdf | BSD-3-Clause |
| BeautifulSoup4 | MIT |
| charset-normalizer | MIT |
| NumPy | BSD-3-Clause |
| ChromaDB (chromadb-client) | Apache-2.0 |
| fastembed | Apache-2.0 |
| youtube-transcript-api | MIT |
| markdown | BSD-3-Clause |
| icalendar | BSD-2-Clause |
| caldav | GPL-3.0-or-later OR Apache-2.0 (dual; used under Apache-2.0) |
| cryptography | Apache-2.0 / BSD-3-Clause |
| bcrypt | Apache-2.0 |
| MCP (Model Context Protocol SDK) | MIT |
| pyotp | MIT |
| qrcode\[pil] | BSD-3-Clause |
| croniter | MIT |
| pytest / pytest-asyncio | MIT / Apache-2.0 |
| duckduckgo-search (optional) | MIT |
| markitdown (optional — Office/EPUB text extraction) | MIT |
| **PyMuPDF** *(optional — form-filling only)* | **AGPL-3.0** — see note below |

## Companion services (interoperated with, not bundled)

Odysseus talks to these over the network/API. They are **not** distributed
with this project; their licenses do not bind this codebase, but they deserve
credit:

- [Ollama](https://github.com/ollama/ollama) — local model serving (MIT)
- [Radicale](https://github.com/Kozea/Radicale) — CardDAV/CalDAV server (GPL-3.0)
- [Dovecot](https://www.dovecot.org/) — IMAP server
- [isync / mbsync](https://isync.sourceforge.io/) — IMAP mailbox sync (GPL-2.0)
- [tmux](https://github.com/tmux/tmux) — terminal multiplexer; Cookbook shells out to it on Linux/macOS for background model downloads and serves (ISC)
- [OpenSSH](https://www.openssh.com/) (`ssh`, `ssh-keygen`, `ssh-copy-id`) — Cookbook shells out to it to manage remote model servers and provision keys (BSD-style permissive)
- Model/API providers: Anthropic, OpenAI, Google (Gemini), DuckDuckGo

---

### License-compatibility notes (for the repo's own LICENSE choice)

The **core ships fully permissive** (MIT-compatible), so the two copyleft
concerns from earlier are resolved:

- **PDF text extraction** now uses **`pypdf`** (BSD-3-Clause) and **encoding
  detection** uses **`charset-normalizer`** (MIT). chardet (LGPL-2.1) has been
  removed entirely.
- **PyMuPDF (AGPL-3.0)** is no longer a core dependency. It is **optional** and
  used *only* by the PDF form-filling feature (`src/pdf_forms.py` and the form
  endpoints in `routes/document_routes.py`), lazy-imported and listed in
  `requirements-optional.txt`. The MIT core runs without it. If you choose to
  install it, AGPL's network clause then applies to *that feature* for your
  deployment (Artifex also sells a commercial PyMuPDF license that lifts this).
- **`caldav`** (Python lib) is **dual-licensed GPL-3.0-or-later OR Apache-2.0**.
  Odysseus uses it under **Apache-2.0**, which is permissive and MIT-compatible.
- **`markitdown`** (Microsoft) is **MIT** and used only as an *optional* dependency for Office/EPUB text
  extraction (`src/markitdown_runtime.py`), lazy-imported with graceful fallback — the MIT core runs without
  it. The cloud `az-doc-intel` extra is deliberately **not** installed, keeping extraction fully local.

---

## Thanks to

Most of Odysseus's code was written *with* AI models, not just by a human.
The project would not exist without them — credit where credit is due:

- **gpt-oss-120b** — the legend that kicked this project off.
- **Qwen3-235B**
- **DeepSeek V3.1 · DeepSeek V4 Pro · DeepSeek V4 Flash**
- **Claude** (Anthropic)
- **Codex** (OpenAI)
- Friends, for helping me debug.
