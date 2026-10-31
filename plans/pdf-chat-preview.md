# Spec: Show PDFs inline in chat

Status: approved 2026-10-29 | Branch: `pdf-export-dev` | Base: 9f896d89 | 2026-10-29
Builds on: `plans/pdf-export-skill.md` (create_pdf, owner-scoped uploads, download cards)

## Objective

When a reply carries a PDF (today: the file `create_pdf` makes), the chat shows
the PDF itself under the reply. The user reads it in place, can expand it, and
can still download it. They no longer have to download a file to find out
whether it holds the right content.

- **User:** the owner, in everyday chats, mostly in Firefox at home and also in
  Chromium-based browsers.
- **Why now:** on 2026-10-29 at 21:02, a "convert this now into a pdf" turn
  (qwen3:8b, approved via the exact-approval card) stored
  `current_context.pdf` (44 KB) and saved it in the reply's
  `metadata.attachments`, but the user saw no file. The PDF also held the
  wrong text: the date/time runtime context instead of the previous answer.
  An inline preview makes both kinds of failure visible at once.

### User stories

1. I ask for a PDF. Under the reply I see the first page of the PDF in a
   preview box, with its file name, size and page count, and a download button.
2. I click **Expand** (or the preview) and the PDF opens in a large overlay. I
   can scroll every page and zoom using the browser's own PDF viewer. Esc or
   the close button returns me to the chat.
3. I reload the page or reopen the chat later. The preview is still there.
4. When the PDF comes from a turn I had to approve, the preview appears on the
   reply after the approval, without a reload.
5. If the browser can't show PDFs inline (PDF viewer turned off, or a mobile
   browser that only downloads), I get today's download card instead of a
   broken box.

### Out of scope

- Inline previews for PDFs the **user** uploads (user-message cards stay as
  they are). The component should be reusable for them later.
- Editing or annotating the PDF (the Documents pane already does that).
- Fixing *what* goes into the PDF (see Open Question 2).

## Approach

Use the browser's built-in PDF viewer in a same-origin `<iframe>`, the same way
the Documents library already previews PDFs (`static/js/documentLibrary.js:1160`,
served by `GET /api/document/{id}/render-pdf` with a framing exception in
`core/middleware.py:99`). No new dependency: Firefox ships pdf.js, Chromium
and WebKit have their own viewers.

Today this is blocked on the server:

- `GET /api/upload/{id}` returns `FileResponse(..., filename=...)`, which sends
  `Content-Disposition: attachment` (`routes/upload_routes.py:520`).
- Every non-exempt response gets `X-Frame-Options: DENY` and
  `frame-ancestors 'none'` (`core/middleware.py:133-150`).

### Changes

1. **Inline serving (server).** `GET /api/upload/{id}?inline=1` serves a PDF
   with `Content-Disposition: inline; filename="<name>"`. It only applies when
   the stored mime is `application/pdf`; other types ignore the flag. Owner
   checks stay exactly as they are (`_verify_upload_access`).
2. **Framing exception (middleware).** For `/api/upload/{id}` with
   `inline=1` and a PDF response, send `X-Frame-Options: SAMEORIGIN` and
   `Content-Security-Policy: default-src 'none'; frame-ancestors 'self'`,
   matching the existing `is_document_pdf_preview` rule. Everything else keeps
   `DENY`.
3. **Preview component (frontend).** In `buildAttachCards(..., { download: true })`,
   a PDF attachment renders as a `.pdf-preview` block:
   - header: file icon, name, size, **Download** and **Expand** buttons
   - body: lazy `<iframe src="/api/upload/{id}?inline=1#view=FitH&toolbar=0">`,
     about 360 px high, created only when the block scrolls into view
     (`IntersectionObserver`), so a long chat doesn't load every PDF at once.
   - **Expand** opens a full-height overlay with the same URL and the
     toolbar on.
   - Fallback: if `navigator.pdfViewerEnabled === false`, or the frame hasn't
     loaded within 5 s, show the existing download card.
4. **Card present live and after reload.** Both `attachments` stream handlers
   (`static/js/chat.js:4723` and `:4849`) and the restore path
   (`static/js/chatRenderer.js:3688`) end up rendering the same component.
   Task 1 confirms which of them misses the post-approval reply from 21:02.
5. **Cache-busting.** Bump the static asset tokens, as task 6 of the export
   spec did.

## Tech Stack

FastAPI + Starlette middleware (Python 3.12), vanilla ES modules in
`static/js`, Playwright 1.62 for browser tests. No new dependencies.

## Commands

```
Python tests:   venv/bin/python -m pytest tests/test_upload_inline_pdf.py tests/test_security_headers_pdf_preview.py tests/test_pdf_attachment_flow.py -q
Upload suites:  venv/bin/python -m pytest tests -q -k "upload"
Node/browser:   node --test tests/assistantAttachmentCards.test.mjs
Firefox e2e:    PHOTO_EDITOR_E2E_BROWSER=firefox npx playwright test --config tests/e2e/playwright.config.js
Run app:        cd ~/odysseus && venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7860
```

(The running app lives in `~/odysseus`; changes made here reach it through a
pull plus a restart.)

## Project Structure

```
routes/upload_routes.py           → inline=1 for PDFs
core/middleware.py                → framing exception for inline upload PDFs
static/js/chatRenderer.js         → .pdf-preview component inside buildAttachCards
static/js/chat.js                 → live attachments events use the component
static/css/…                      → preview + overlay styles (existing chat stylesheet)
tests/test_upload_inline_pdf.py   → new: headers, owner checks, non-PDF ignores inline
tests/test_security_headers_pdf_preview.py → extend: upload preview framing rule
tests/assistantAttachmentCards.test.mjs    → extend: preview renders, expand, fallback
```

## Code Style

Match the surrounding code: small exported helpers in `chatRenderer.js`, DOM
built with `createElement` (no `innerHTML` for file names), and the existing
`attach-card` class names as the base. Example of the shape:

```js
function buildPdfPreview(att) {
  const wrap = document.createElement('div');
  wrap.className = 'pdf-preview';
  const name = document.createElement('span');
  name.className = 'attach-card-name';
  name.textContent = att.name;             // never innerHTML
  // … header buttons, lazy iframe on intersection, fallback to buildAttachCards([att], { download: true })
  return wrap;
}
```

Server style follows `document_routes.py`'s `render-pdf` handler: a small
branch at the response, no new route.

## Testing Strategy

- **pytest (server):** with `inline=1`, the owner gets
  `Content-Disposition: inline`, `X-Frame-Options: SAMEORIGIN` and
  `frame-ancestors 'self'`. Without it, today's headers are unchanged. Another
  user still gets the existing denial. A non-PDF upload with `inline=1` keeps
  `attachment` and `DENY`.
- **Node + Playwright (frontend):** an assistant message whose metadata holds a
  PDF ref renders `.pdf-preview`, with an iframe pointing at `?inline=1`, once
  scrolled into view. Expand opens the overlay and Esc closes it. With
  `pdfViewerEnabled=false` it renders the download card. The live
  `attachments` event and the reload path give the same DOM. Run in Chromium
  and Firefox (WebKit if available).
- **Regression:** the post-approval case from 21:02 (attachments arriving on
  the continuation reply) shows the preview live; this gets a test of its own.
- **Manual:** in the running app, ask qwen2.5 and qwen3:8b for a PDF in
  Firefox, check that the preview shows the right first page, expand it,
  reload, and download.

## Boundaries

- **Always:** keep owner checks unchanged; build DOM with `textContent`; test
  both the live and the reload path; run the upload and security-header suites.
- **Ask first:** adding a PDF rendering library (pdf.js bundle, PyMuPDF page
  thumbnails); loosening framing for anything other than PDF uploads served
  with `inline=1`; previews on user-uploaded PDFs.
- **Never:** set `frame-ancestors` wider than `'self'`; serve non-PDF uploads
  inline; let the iframe run script from the upload response (CSP stays
  `default-src 'none'`).

## Success Criteria

1. After a successful `create_pdf`, the reply shows the PDF's first page
   inline within 2 s of the attachment event, in Firefox and Chromium.
2. Expand shows all pages; Esc closes it; Download still saves `<name>.pdf`.
3. The preview is identical after a reload and on the post-approval reply.
4. Another user requesting `/api/upload/{id}?inline=1` is still denied.
5. Non-PDF uploads and all other routes keep `X-Frame-Options: DENY`.
6. With the browser PDF viewer off, the user sees the download card, never an
   empty frame.
7. Upload, security-header and chat-renderer suites stay green.

## Open Questions

1. **Did the download card show at all at 21:02, and did it show after a
   reload?** The attachment is saved on that reply, so a reload should show
   at least the card. If it does, only the live post-approval path is broken
   (change 4). If it doesn't, the restore path is broken too.
2. ~~**Wrong PDF content.**~~ Fixed first, as its own change: `create_pdf`
   drops date/time context lines and treats content made only of them as
   empty. The previous-answer fallback uses only the reply to the message
   before the request, skipping the turn's approval prompt. If that reply is a
   refusal or a question, no PDF is made and the model is told to write the
   content or ask.
3. **Preview height:** first page only (about 360 px, scroll inside the frame)
   or the full width at A4 ratio? Draft assumes about 360 px plus Expand.
