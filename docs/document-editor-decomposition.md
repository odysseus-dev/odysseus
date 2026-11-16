# Document Editor Modular Decomposition

## Overview

The Odysseus Document Editor was decomposed from a monolithic file (`static/js/document.js`) into modular, bounded frontend components under `frontend/src/features/documents/` with compiled distributions published to `static/js/document/`.

The public API facade (`documentModule`) remains backward-compatible with existing host consumers and tests while internal state, domain rules, persistence, and UI lifecycles are strictly encapsulated.

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
   - `sourceEditor/`: Code editor, syntax highlighting sync, line numbering, and timer management.
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

2. **Document Creation and Navigation Reconciliation**
   - Rapid navigation while document creation is in flight (`createDocument` or `_autoCreateFromInput`) increments the navigation generation counter.
   - When the server response returns, the document is registered and added to tabs for the active session, but does not switch active documents or steal user focus.

3. **Email Content Preservation and Sanitization**
   - Outgoing email bodies preserve plain text containing literal angle brackets (e.g. `a < b` or `<user@domain>`) without premature truncation.
   - HTML-to-text conversion preserves paragraph (`\n\n`) and line break (`\n`) structure.
   - Supported named and numeric entities (including Latin-1 and currency entities) decode safely.
   - Dangerous tags (`<script>`, `<style>`) and their contents are stripped unconditionally.

4. **Diff Calculation and Scalability**
   - `diffLines` detects unchanged common prefix and suffix lines before computing the LCS matrix on the middle slice.
   - Localized changes in large documents (e.g. 800+ lines) preserve exact per-hunk diffs without triggering the full-document delete/insert fallback.
   - Genuine whole-document changes exceeding 500,000 comparison cells continue to use the bounded fallback to prevent freezing the UI.

5. **Safe URL Validation**
   - Rich-text link normalization supports RFC 6068 multi-recipient mailto URLs (`mailto:a@b.com,c@d.com`) and RFC 3966 telephone parameters (`tel:+1-555-1234;ext=12`).
   - Unsafe schemes (`javascript:`, `data:`, `file:`) and control characters remain rejected.

6. **Save Behavior on HTTP 404**
   - If a document returns HTTP 404 during persistence, auto-save is cancelled to prevent repeated failing requests for deleted documents.

## Verification

The module architecture is validated through repository-contained tests:

- `python scripts/build_frontend.py --check`: Ensures maintained modules in `frontend/src/` match published modules in `static/js/`.
- `node scripts/check_frontend.mjs`: Enforces architectural layering, dependency rules, and export constraints.
- `node scripts/check_document_references.mjs`: Verifies cross-module symbol imports, exports, and facade compatibility.
- `node --test tests/domainModules.test.mjs`: Validates pure domain logic, entity decoding, and URL rules.
- `node --test tests/document*.test.mjs`: Validates individual UI component and application workflow behavior.
