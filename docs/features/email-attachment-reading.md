# Feature: Email Attachment Reading (PDF + OCR)

## Summary
The agent can now read and summarize email attachments inline, including scanned/image-only PDFs, via the `read_email_attachment` tool. OCR fallback is available when `pypdf` text extraction returns empty content.

## Lessons Learned

### 1. Tool schema registration
- MCP tools exposed by a builtin Python server are **not** automatically visible to the model.
- Adding a tool to the MCP server is necessary but **not sufficient**; it must also be added to `FUNCTION_TOOL_SCHEMAS` in `src/tool_schemas.py` to appear in the model's tool list.
- The MCP schema filter in `src/agent_loop.py` must match both qualified names (`mcp__email__read_email_attachment`) and bare names (`read_email_attachment`) against `_relevant_tools`.

### 2. Email domain context preservation
- Follow-up turns can lose the `email` domain if they don't contain explicit keywords like "email" or "mail".
- Context-aware preservation in `src/agent_loop.py` keeps email tools available when `active_email` is set in the session or when the email domain is active.
- The `_DOMAIN_TOOL_MAP["email"]` list must include all attachment-related tools (`download_attachment`, `read_email_attachment`) so they are seeded deterministically when the domain fires.

### 3. PDF extraction strategy
- `pypdf.extract_text()` works for normal PDFs but returns empty strings for scanned/image-only PDFs.
- An OCR fallback is required for scanned documents. The implementation tries:
  1. `pdf2image` + `pytesseract` (best quality)
  2. `PyMuPDF` page rendering + `pytesseract`
  3. Returns an explicit `error` field if OCR is unavailable so the model can pivot to `download_attachment`.
- **Important:** When checking whether a PDF has extractable text, check per-page non-emptiness (`has_real_text`), not just `text_content.strip()`, because page headers can make the joined string truthy even when every page is empty.

### 4. Permission/privilege model
- All email tools are included in `NON_ADMIN_BLOCKED_TOOLS` in `src/tool_security.py`.
- Non-admin users will see `read_email_attachment` in the schema list but tool execution will be blocked server-side.
- For testing/feature use, the account must be an admin (`is_admin: true` in `data/auth.json`).

### 5. Model behavior quirks (Gemma 4B 12B)
- The model sometimes emits raw `<|tool_call>...<tool_call|>` text into user-facing responses instead of proper native function calls.
- Follow-up questions that don't trigger the `email` domain are classified as `low_signal=True domains=[]`, causing the agent to re-list inboxes from scratch instead of continuing the analysis.
- This is a limitation of small local models and should be addressed separately via prompt engineering or model upgrade.

## Files Modified
- `src/agent_loop.py` — context preservation, MCP schema filter, domain rules, debug logs
- `src/tool_schemas.py` — added attachment/draft email tools to `FUNCTION_TOOL_SCHEMAS`
- `src/tool_index.py` — keyword hints for attachments and email search
- `src/tool_security.py` — added new email tools to `BUILTIN_EMAIL_TOOLS` and `NON_ADMIN_BLOCKED_TOOLS`
- `mcp_servers/email_server.py` — `_read_email_attachment`, `_try_pdf_ocr`
- `tests/test_email_attachment_reading.py` — plain text, HTML, PDF, unsupported format tests
- `requirements-optional.txt` — optional OCR dependencies
- `Dockerfile` — conditional OCR system package installation

## How to Enable OCR in Docker
```bash
docker compose build --build-arg INSTALL_OPTIONAL=true odysseus
docker compose up -d odysseus
```
Without this, scanned PDFs will return an `error` field instructing the user to download the file.

## Known Limitations
- Follow-up drilling into contract details requires the `email` domain to remain active; otherwise the model resets.
- Non-admin users cannot execute email tools (by design).
- OCR quality depends on `tesseract` language data and image resolution.
