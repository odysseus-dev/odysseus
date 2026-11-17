# Document Editor Modular Decomposition

## Overview

The Odysseus Document Editor was decomposed from a monolithic file (`static/js/document.js`) into frontend modules under `frontend/src/features/documents/`, published byte-for-byte to `static/js/document/`.

The public API facade (`documentModule`) preserves existing host imports and exports. Domain rules are independent of application state and host UI modules. Application state owns the shared document map and scalar transitions; feature modules still mutate document records. Command and persistence hooks connect application operations to the pane. Persistence retains standalone DOM fallbacks and some compatibility coupling.

## Architecture and Module Boundaries

The module hierarchy follows clear ownership layers:

1. **Application Layer (`application/`)**
   - `documentState.js`: Authoritative in-memory state (`docs`, `activeDocId`, `isOpen`, delete tombstones).
   - `documentCommands.js`: High-level command interfaces (`openPanel`, `closePanel`, `switchToDoc`, `renderTabs`).
   - `documentSession.js`: Session attachment, tab registration (`addDocToTabs`), and lifecycle cleanup.
   - `persistence.js`: Serialized document autosave, queue management, and save-state tracking.

2. **Domain Layer (`domain/`)**
   - `documentKinds.js`: Language detection, HTML-to-plain-text conversion, and standards-compatible entity decoding.
   - `emailMessage.js`: RFC 822 email header parsing, base64 wrapper decoding, and attachment metadata.
   - `richTextRules.js`: URL normalization (mailto, tel, http/https), formatting palette, and inline rules.

3. **UI Layer (`ui/`)**
   - `panel/`: Editor pane templates, DOM mounting, and lifecycle event wiring.
   - `tabs/`: Tab bar rendering, keyboard navigation, drag reordering, and inline title editing.
   - `toolbar/`: Document toolbar buttons, dropdown menus, and formatting actions.
   - `editor/`: Source editor, syntax highlighting sync, line numbering, and timer management.
   - `richEditor/`: WYSIWYG editor sync, contenteditable event handling, and format commands.
   - `email/`: Email composer, recipient autocomplete, local draft caching, and send workflows.
   - `diffViewer.js`: Line-level LCS diff viewer with prefix/suffix preservation and chunk review.
   - `documentFlows.js`: Document creation, loading, closing, and deletion workflows.
   - `documentActions.js`: User actions (run code, copy, update title, change language).
   - `host.js`: Host adapter binding to core application dialogs, toasts, and runners.

## Behavioral Changes and Operational Contracts

The refactoring incorporates the following behavioral contracts:

1. **Document Title Updates and Persistence**
   - Clearing a title in the UI persists an empty title (`{ title: "" }`) to the server.
   - The server treats empty titles as untitled, enabling subsequent automatic titling (`if_untitled: true`) from content headings.
   - If the server rejects a title update (such as HTTP 409 or network failure), the client automatically rolls back the tab title and input field to the previous persisted title.
   - Manual title requests run in order per document. Conditional updates use the last acknowledged title; failed or older responses cannot roll back a newer edit. Automatic titling waits while manual edits are pending.

2. **Document Creation and Navigation Reconciliation**
   - Rapid navigation while document creation is in flight (`createDocument` or `_autoCreateFromInput`) increments the navigation generation counter.
   - A stale successful response is registered for its originating session without changing the active document or reading another document's editor. Its tab is visible when that session is active.

3. **Email Content Preservation and Sanitization**
   - Outgoing email bodies preserve plain text containing literal angle brackets (e.g. `a < b` or `<user@domain>`) without premature truncation.
   - HTML-to-text conversion preserves paragraph (`\n\n`) and line break (`\n`) structure.
   - Supported named and numeric entities (including Latin-1 and currency entities) decode safely.
   - Browsers decode additional HTML5 references through a detached parser receiving only entity tokens. Without a browser parser, the domain converter supports its explicit named-entity table and Unicode numeric references, preserving unknown names.
   - Each HTML body is converted once so escaped literal tags remain text and malformed fragments cannot cause recursive conversion. Send, Save Draft, and Schedule treat rich-editor `innerText` as plain text; draft/send HTML rebuilt from that text is escaped. Newly decoded email wrapper bodies are sanitized separately.
   - Dangerous tags (`<script>`, `<style>`) and their contents are stripped unconditionally.

4. **Diff Calculation and Scalability**
   - `diffLines` detects unchanged common prefix and suffix lines before computing the LCS matrix on the middle slice.
   - Localized changes in large documents (e.g. 800+ lines) preserve exact per-hunk diffs without triggering the full-document delete/insert fallback.
   - Genuine whole-document changes exceeding 500,000 comparison cells continue to use the bounded fallback to prevent freezing the UI.

5. **Safe URL Validation**
   - Rich-text link normalization supports RFC 6068 multi-recipient mailto URLs (`mailto:a@b.com,c@d.com`) and RFC 3966 telephone parameters (`tel:+1-555-1234;ext=12`).
   - Unsafe schemes (`javascript:`, `data:`, `file:`) and control characters remain rejected.

6. **Persistence and email recovery**
   - A failed save, including HTTP 404, retains the local draft and reports an error instead of removing the tab. A later edit or explicit save may retry.
   - Email body and envelope edits participate in dirty tracking. Explicitly cleared local Cc/Bcc fields override older server values when restoring a local draft.

7. **Table and panel interactions**
   - Rich tables support splitting row-spanning cells and a tested merge-down command; the toolbar does not currently expose merge-down.
   - Tab selection, version history, and closing use separate controls, including visible keyboard focus. These DOM and CSS changes require the PR's existing visual evidence.

## Verification

The module architecture is validated through repository-contained tests:

- `python scripts/build_frontend.py --check`: Ensures maintained modules in `frontend/src/` match published modules in `static/js/`.
- `node scripts/check_frontend.mjs`: Enforces architectural layering, dependency rules, and export constraints.
- `node scripts/check_document_references.mjs`: Verifies cross-module symbol imports, exports, and facade compatibility.
- `node --test tests/domainModules.test.mjs`: Validates pure domain logic, entity decoding, and URL rules.
- `node --test tests/document*.test.mjs`: Validates individual UI component and application workflow behavior.
