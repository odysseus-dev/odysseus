# PDF text extraction and the PyMuPDF / VL-model fallback

When you upload a PDF to chat, Odysseus needs to (a) extract the text so
the model can see it, and (b) render the pages so the document viewer
can show them. These two paths use different dependencies and have
different failure modes. This doc covers both, what they require, and
what to do when something goes wrong.

## TL;DR

| Path | Dependency | What it does | When it fails |
|---|---|---|---|
| Text extraction | `pypdf` (in `requirements.txt`, MIT) | Pulls text out of the PDF's text layer | Scanned PDFs (no text layer), custom font encodings, text stored as paths |
| Image-OCR fallback (per page image) | `pypdf` + configured VL model | OCRs embedded images when text is short | No VL model configured; page has no enumerated images |
| **Full-page OCR fallback** (scanned pages) | `pypdf` + `PyMuPDF` + configured VL model | Renders the whole page and OCRs it | PyMuPDF not installed; no VL model configured; VL disabled |
| Document viewer (render pages) | `PyMuPDF` (AGPL-3.0) | Server-renders each page to PNG | PyMuPDF not installed → "PDF viewer requires PyMuPDF" banner |

The full-page OCR fallback is new. It fires when `pypdf` returns no text
for a page **and** `pypdf` couldn't enumerate any useful images — the
classic "scanned PDF" or "text-as-paths" case.

## Requirements

### Core (already in `requirements.txt`)

- `pypdf` — used for text extraction and image enumeration. Always
  installed by both Docker and native installs.

### Optional (in `requirements-optional.txt`)

- `PyMuPDF` (`fitz`) — AGPL-3.0, used for:
  - Document viewer: rendering each PDF page to PNG so the editor can
    overlay form fields and annotations.
  - Full-page OCR fallback: rendering a text-less page so the VL
    model can read it.

- A vision-capable model configured in **Settings → Vision**
  (auto-detected by default — see "Auto-detected vision models" below).
  Used for both the per-image and full-page OCR paths.

### Docker

The bundled image installs `PyMuPDF` automatically (see
[`Dockerfile`](../Dockerfile)) so the viewer and full-page OCR work
without extra setup. PyMuPDF is AGPL-3.0 — using the bundled image
accepts that license. Native users opt in with
`pip install -r requirements-optional.txt`.

## Symptom → fix

### "processed but no readable content found"

Chat attached a PDF, but the model was told the file had no content.

1. **Open the PDF in a desktop reader.** If you can select text with
   your cursor, the PDF has a text layer — `pypdf` should have
   extracted it. If you can't, it's a scanned / image-only PDF.
2. **For a scanned PDF:** the full-page OCR fallback is what helps.
   Make sure:
   - `PyMuPDF` is installed (Docker: already; native:
     `pip install PyMuPDF` or `pip install -r requirements-optional.txt`).
   - A vision model is reachable. In **Settings → Vision**, set the
     model name to something you actually serve (or rely on
     auto-detect — see below).
3. **Re-run extraction on an already-imported PDF:** the document
   viewer has a "Re-extract text" button that re-runs `_process_pdf`
   against the source upload and merges the result into the doc's
   markdown. Same `POST /api/document/{id}/extract-pdf-text` endpoint
   is exposed for scripted use.
4. **Still failing?** Check the server logs for `Full-page OCR
   fallback failed` or `VL model unavailable`. The most common
   cause is a vision endpoint that's reachable for chat but not for
   the internal VL helper (e.g. base-URL misconfigured, or the model
   isn't actually vision-capable).

### "PDF viewer requires PyMuPDF"

The document viewer couldn't render the source PDF. Install
PyMuPDF (Docker images already have it; native users:
`pip install -r requirements-optional.txt`) and reload the page.

### Kimi / Claude / your vision model says "I can't see the PDF"

Same root cause as "processed but no readable content found" — the
chat path gave the model an empty placeholder, so the model correctly
refused to fabricate content. Fix the extraction path (above) and
re-send.

## Auto-detected vision models

When **Settings → Vision → Vision model** is left blank, Odysseus
auto-detects a vision-capable model by trying each candidate in order
against your configured endpoints (`src/document_processor.py:_resolve_vl_model`):

```
gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4.1-mini,
claude-sonnet-4-5-20250929, claude-opus-4-20250514,
gemini-2.0-flash, gemini-2.5-pro,
MiniCPM-V-4.6-Thinking, llava, pixtral, qwen2-vl
```

`is_vision_model()` (used elsewhere to decide whether to pass
images through to the chat model) is a substring match against
`gpt-4o`, `claude-sonnet`, `claude-opus`, `claude-haiku`, `gemini`,
`vision`, `llava`, `pixtral`, **`minicpm`**, `internvl`, `cogvlm`,
`qwen-vl`, `qwen2-vl`, `qwen3-vl`, `qwen3vl`, `glm-4.5v`, `glm-4.6v`,
`glm-5v`. MiniCPM-V-4.6-Thinking is covered.

If none of the candidates resolve on your endpoints, set a vision
model explicitly in Settings.

## Operational notes

- **Cost:** the full-page OCR fallback calls the VL model up to 5
  pages per PDF (see `_PDF_FULLPAGE_OCR_PAGE_CAP` in
  `src/document_processor.py`). For a 20-page scanned PDF expect
  ~5 model calls. The inline text is then capped at 15,000 chars
  to keep the chat prompt small.
- **AGPL implications:** if you serve the bundled Docker image
  publicly, PyMuPDF's AGPL-3.0 obligations apply. Native users who
  don't want this can stay on `pypdf`-only by skipping
  `requirements-optional.txt`; the chat text-extraction path
  still works, the document viewer doesn't render pages, and the
  full-page OCR fallback is a no-op.
- **Tested in:** `tests/test_document_processor_pdf.py` covers
  the new branch (full-page OCR fires, fast path preserved,
  graceful degradation when PyMuPDF is missing).
