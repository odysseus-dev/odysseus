# Spec: Chat PDF Export ("make a pdf")

Status: approved 2026-10-29 (ReportLab + bundled DejaVu fonts) | Branch: `new-skill-making-pdfs` | Base: dev@1e4cd16a | 2026-10-29

## Objective

In an Odysseus chat, a request such as "make a pdf" or "turn those instructions
into a pdf" produces a well-designed PDF of the relevant earlier chat content
and offers it as a download that the user saves to their machine.

- **User:** the owner, in everyday chats (how-tos, fixes, summaries, translations).
- **Why now:** these requests currently go nowhere. On 2026-10-29, "can you make
  that into a pdf and translate it into hungarian" resolved to
  `selected_tools=[]`. The model received no tools and replied with plain
  prose that summarized the system context instead of the conversation.
- **Design constraint:** it must work with the local models in use (qwen2.5,
  qwen3.8, DeepSeek-R1-8B). The model chooses *what* goes in the PDF; the app
  owns *how it looks*, through one fixed template.
- **Browser constraint:** the user works in Firefox at home. Rendering is
  server-side, so no viewer browser takes part in it. The card and the
  download must work in Firefox as well as Chromium-based browsers. The
  renderer must not need a browser installed on the host.
- **Decided:** A4 pages; delivered as a chat attachment the user downloads on
  demand (not added to the Documents library).

### User stories

1. After a how-to answer, I say "turn those instructions into a pdf". I get a
   download card for a PDF containing those instructions, nicely laid out.
2. I say "make a pdf of that" right after any assistant answer. The PDF holds
   that answer.
3. I say "translate it to Hungarian, then make a pdf". The chat translates as
   usual, and the PDF holds the translated text.
4. I reopen the session later and the download card is still there and still works.

### Out of scope (v1)

- Designs that adapt to the content (a recipe card, a manual layout): v2.
- Editing a PDF after it is generated.
- Filling in existing PDF forms (`src/pdf_forms.py` already covers that).
- Translation inside the tool. The chat translates first.
- Exporting a whole session (the existing `/session/{sid}/export` covers that;
  adding `fmt=pdf` there is a possible follow-up, not this feature).

## What exists today (inspected)

| Area | Current state | Relevance |
|---|---|---|
| Routing / authority | `src/turn_contract.py` maps request text to capability families; `src/agent_runtime/authority.py` grants tools from them. No family recognizes "make a pdf". | Must add a route, or the tool is never offered or granted. Same failure class as the web-search fix (`9e3a87b0`). |
| Tool registry | `src/agent_tools/__init__.py` (`TOOL_HANDLERS`), schemas in `src/tool_schemas.py`, index in `src/tool_index.py`. | The new tool registers here. |
| Markdown → HTML | `markdown` is a core dependency (used by `src/visual_report.py`). | Reuse for the body. |
| PDF output today | Only DOCX→PDF via LibreOffice (`routes/document/document_routes.py:473`; not installed on this host), and the form-fill export (`/api/document/{id}/export-pdf`). PyMuPDF is optional and AGPL. | No general Markdown→PDF renderer exists. |
| Headless browser | `src/agent_tools/web_tools.py:_browser_executable_candidates()` searches Playwright-style cache layouts only. On the owner's Mac it returns `[]` even though Google Chrome.app is installed. | Rejected as the renderer: it depends on the host and fails on this machine today. |
| File delivery | Uploads are stored per owner and served by `GET /api/upload/{id}` (owner-checked, `HEAD` supported). Chat renders `.attach-card`s from message `metadata.attachments` (`static/js/chatRenderer.js:buildAttachCards`). | Store the PDF as an owned upload and attach it to the assistant message. |
| Session export | `routes/session_routes.py:export_session`: md/txt/json/html, no pdf. | Not reused in v1. |

## Proposed Design

1. **Route:** add a narrow request rule ("pdf" together with make / turn into /
   export / save / create / convert, plus German "als pdf" / "pdf machen") that
   selects the new tool. Follow-ups referring to "that", "those instructions" or
   "it" must resolve the *previous assistant turn*, not the system context.
2. **Tool `create_pdf`:** arguments are `{title, content_markdown, filename?}`.
   The model passes the Markdown of what should go in the PDF (usually copied
   from an earlier assistant message). If `content_markdown` is empty, the tool
   uses the last assistant message, so weak models still succeed.
3. **Render:** parse the Markdown into blocks (headings, paragraphs,
   ordered/unordered lists, fenced code, tables, emphasis, links) and lay them out
   with **ReportLab Platypus** in one fixed A4 template: title block, date,
   body typography, numbered steps, shaded monospaced code blocks, ruled tables,
   and "Page N of M" in the footer. This is pure Python and needs no browser or
   system libraries. An embedded Unicode TTF font (e.g. DejaVu Sans/Mono,
   permissive license) is required, because ReportLab's built-in fonts can't
   show Hungarian ő/ű.
4. **Deliver:** save the PDF as an upload owned by the requesting user, and add
   it to the assistant message's `metadata.attachments`, so the existing
   attach-card and the owner-checked download route serve it. The tool result
   tells the model the file was created, so it doesn't claim otherwise.
5. **The reply is the PDF, for every model.** (Deviation, 2026-10-29: the
   first design relied on the model calling `create_pdf`. With the agent
   prompt and tools in view, qwen2.5 and qwen3:8b instead planned
   "conversions", asked for a workspace or said they can't make PDFs, and a
   prompt note didn't change that.)
   - **Plain PDF requests** (`pdf_export`, optionally with the `shell_files`
     family that rides along on "make that into a pdf"): the agent loop hands
     the turn to one tool-free writer call (`_stream_pdf_writer_turn`). It
     sees a short "you are the document writer" prompt plus the last 12
     plain chat messages, without system prompts or injected context (memory,
     date block). Its reply goes only into the PDF (a leading `# ` heading
     becomes the title); the chat shows a `create_pdf` tool trace while it
     is written. A second short call then gets the request and the document
     and writes the chat reply: two to four sentences, in the user's
     language, on what the PDF contains. A one-line question back makes no
     PDF and is shown as the reply. Live check, 2026-10-29: 6/6 correct
     documents and explanations on qwen2.5 and qwen3:8b (new content EN/DE,
     and "make that into a pdf").
     Known gap: later turns see the explanation, not the document, so "add a
     section to that pdf" can't build on the PDF's text yet.
   - **Requests that need data** (notes, email, web, an open document) keep
     the agent loop. If it ends without a `create_pdf` file, the final reply
     is rendered into the PDF the same way. Untrusted context doesn't block
     this, since it only renders the visible reply into the owner's uploads.
6. **Safety:** model-provided Markdown is untrusted. Escape or strip raw HTML
   before templating. The renderer loads no network resources and runs no
   scripts. Size limits apply to both input and output.

## Tech Stack

Python 3.12 / FastAPI backend, vanilla ES modules frontend. New dependencies
(approved): `reportlab` (BSD) in `requirements.txt`, and
bundled DejaVu Sans + Sans Mono TTFs (Bitstream Vera derived, free license) under
`assets/fonts/`.

## Commands

```
Test (targeted):  python -m pytest -q -p no:cacheprovider tests/test_pdf_export*.py tests/test_turn_contract.py
Test (routing):   grep -rlE "turn_contract|create_request_authority" tests --include='test_*.py' | xargs python -m pytest -q -n auto
JS tests:         node --test tests/<name>.test.mjs
Run app:          ./start-macos.sh   (http://127.0.0.1:7860)
```

## Project Structure

```
src/pdf_export.py                  → Markdown→HTML→PDF renderer + template (new)
src/agent_tools/document_tools.py  → CreatePdfTool handler (or a new pdf_tools.py)
src/agent_tools/__init__.py        → register create_pdf
src/tool_schemas.py, src/tool_index.py → schema + index entry
src/turn_contract.py               → "make a pdf" routing rule
src/agent_runtime/authority.py     → only if family→tool mapping needs it
static/js/chatRenderer.js          → only if assistant attach-cards need a fix
tests/test_pdf_export.py           → renderer + tool tests (new)
tests/test_turn_contract.py        → routing tests
```

## Code Style

Match the surrounding modules: module-level compiled regexes with a comment
saying *why*, small private helpers, tool handlers returning
`{"output": ..., "exit_code": 0}` or `{"error": ..., "exit_code": 1}`. Example
from the routing fix:

```python
if (not families and _EXPLICIT_WEB_LOOKUP.search(text)
        and not _LOCAL_LOOKUP_TARGET.search(text)):
    families.add("search_browser")
```

## Testing Strategy

- **Routing (pytest):** the phrasings in the user stories select `create_pdf` and
  authority permits it. Near-misses ("what is a pdf", "read this pdf", "fill this
  pdf form") do not.
- **Renderer (pytest):** given Markdown with headings, steps, code, a table and
  non-ASCII text (Hungarian/German), the output is a valid PDF with ≥1 page, the
  title on page 1, and page numbers. Raw `<script>`/HTML in the input is inert.
- **Tool (pytest):** an empty `content_markdown` falls back to the last assistant
  message. The result is an owner-scoped upload, and the attachment ref lands in
  message metadata.
- **Delivery:** `GET`/`HEAD /api/upload/{id}` serves the PDF to its owner and
  denies other users (reuse the upload tests' fixtures).
- **Firefox:** the download card renders on an assistant message and the click
  saves `<title>.pdf` in Firefox. Use the repo's Playwright e2e setup
  (`PHOTO_EDITOR_E2E_BROWSER=firefox`) or a node `firefox.launch()` test, plus a
  Chromium run.
- **Manual:** in the running app with qwen2.5, run the four user stories in
  Firefox; open each PDF and judge it by eye.

## Boundaries

- **Always:** keep layout in the template, not the prompt; escape untrusted
  content; write tests first for routing changes; run the routing suite.
- **Ask first:** adding any dependency (WeasyPrint, ReportLab, Playwright);
  changing shared authority families; changing upload storage or retention.
- **Never:** let the model emit raw HTML/CSS into the PDF; fetch remote assets
  while rendering; widen web or shell authority as a side effect; edit `specs/`
  unless asked.

## Success Criteria

1. Each of the four user stories works end-to-end with qwen2.5 in the local app.
2. A request to its download card succeeds within 30s of asking (cold start
   included) for a ~2-page answer.
3. The PDF has a title, a date, readable body text, preserved numbered lists,
   monospaced code blocks, rendered tables, correct non-ASCII text, and
   page numbers.
4. Only the owner can download it; it survives a page reload.
5. The download card works and saves the file in Firefox and in a Chromium-based browser.
6. Rendering works on a host with no browser installed.
7. No regressions in the routing/authority suite or the upload suites.

## Open Questions

1. ~~Approve the new dependency.~~ Approved: `reportlab` plus bundled DejaVu
   fonts. Rejected alternatives: headless Chromium (depends on the host; finds
   nothing on this Mac) and WeasyPrint (needs Pango and other system libraries
   from Homebrew).
2. **Known gap: compact tool surface.** The attachment event is emitted by
   the main agent loop (`stream_agent_loop`). Turns that run on the compact
   preview runtime (`clean_agent_preview.stream_preview`, used when a model's
   tool surface is set to "compact") do not emit it yet. PDF turns route as
   `selection_mode="routed"`, so the default setup is covered.
3. **Check during planning (no decision needed).** `buildAttachCards` is
   confirmed for user messages. Whether assistant messages render
   `metadata.attachments` is unverified; if they don't, a small
   `chatRenderer.js` change is needed.
