# Frontend Module Organization Summary

> **Scope:** This document describes the architecture of the Odysseus no-build
> frontend. The app is a collection of native ES6 modules loaded from
> `static/`. The authoritative source is the current `static/js/` tree and the
> top-level orchestrator `static/app.js`.

---

## 1. Top-level Application Orchestrator

### `static/app.js`
*Main application entry point.*

- Imports all feature modules.
- Exposes a few modules on `window` for legacy inter-module reachability
  (`themeModule`, `sessionModule`, `uiModule`, `adminModule`, `cookbookModule`).
- Patches `fetch` so any `401` redirects the user to `/login`.
- Fetches the default chat configuration and handles deep-link route openers
  (`/notes`, `/calendar`, `/email`, `/memory`, `/gallery`, `/cookbook`, `/library`, `/tasks`).
- Wires global event listeners: chat-history scrolling, popups, Escape handling,
  drag-and-drop/paste attachment handling, transcription export, sidebar toggles,
  rail/tool buttons, and session sorting.
- Loads auth status and applies per-user privilege restrictions.

### `static/index.html`
*SPA shell.* Loads `app.js` as a module, includes the theme-aware inline script,
and defines the DOM skeleton that the modules populate (chat history, composer,
sidebar, icon rail, modals).

---

## 2. Core Foundation Modules

These are imported first and used across most features.

| Module | Primary Exports | Responsibility |
|---|---|---|
| **`ui.js`** | `showToast`, `showError`, `el`, `copyToClipboard`, `scrollHistory`, `setAutoScroll`, `autoResize`, `debounce`, `esc` | Shared UI helpers, toast notifications, scroll behavior, element accessor, text escaping. |
| **`storage.js`** | `default` storage wrapper | LocalStorage helpers and toggle state persistence. |
| **`markdown.js`** | `mdToHtml`, `processWithThinking`, `squashOutsideCode`, `normalizeThinkingMarkup`, `extractThinkingBlocks`, `hasUnclosedThinkTag`, `startsWithReasoningPrefix` | Markdown→HTML, thinking/reasoning block parsing, code-block normalization. |
| **`spinner.js`** | `create`, `createWhirlpool` | Loading/spinner factories for streaming and tool cards. |
| **`keyboard-shortcuts.js`** | `initKeyboardShortcuts` | Global keyboard shortcut wiring. |
| **`sidebar-layout.js`** | `initSidebarLayout`, `syncRailSide` | Wide sidebar ↔ icon-rail layout behavior. |
| **`section-management.js`** | `initSectionCollapse`, `initSectionDrag` | Collapsible/draggable sidebar sections. |
| **`modalManager.js`** | side-effect import | Unified minimize/restore behavior for floating tool modals. |
| **`tileManager.js`** | side-effect import | Desktop window tiling and snap-to-edge behavior. |
| **`windowDrag.js`** | `makeWindowDraggable` | Drag support for floating panels. |
| **`modalSnap.js`**, **`toolWindowZOrder.js`**, **`windowResize.js`** | — | Modal snapping, z-index management, resize handles. |

---

## 3. Chat Pipeline

The largest and most central subsystem. Chat submission → backend SSE → progressive rendering of text, tools, research, documents, and UI events.

| Module | Responsibility |
|---|---|
| **`chat.js`** | Main chat controller. Handles `handleChatSubmit`, stops/continues, builds `FormData`, posts to `/api/chat_stream`, reads the SSE stream, and dispatches each JSON event to the appropriate renderer. Tracks background streams, stalls, auto-recovery, and multi-round agent state. |
| **`chatStream.js`** | Helpers shared between streaming consumers: browser notifications, background-stream completion toasts, and `ui_control` event handling. |
| **`chatRenderer.js`** | Message DOM construction: `addMessage`, role labels, model route labels, color coding, footers, metrics, code blocks, sources boxes (`web`/`research`/`RAG`), findings box, images, report links, ask-user cards, welcome screen, and transcript utilities. |
| **`streamingRenderer.js`** | Incremental streaming renderer used by `chat.js`. Freezes finalized DOM blocks and only re-renders the growing tail to avoid flicker and O(N²) re-parsing. |
| **`streamingSegmenter.js`** | Splits a token stream into display units (text vs code fences) for `streamingRenderer.js`. |
| **`slashCommands.js`** | Slash-command registry (`/help`, `/setup`, etc.), parsing, and dispatch handlers. Exported functions are consumed by `chat.js` and `slashAutocomplete.js`. |
| **`slashAutocomplete.js`** | Composer autocomplete popup for `/` commands. |
| **`composerArrowUpRecall.js`** | Recall last user message with `↑` on an empty composer. |
| **`assistant.js`** | Assistant/persona behaviors and message styling helpers. |
| **`tts-ai.js`** | AI text-to-speech manager, enqueueing, streaming TTS, and playback button injection. |
| **`voiceRecorder.js`** | Voice recording from the composer microphone. |
| **`fileHandler.js`** | Attachment picker, paste/drop handling, upload, attachment strip rendering, pending-file management. |
| **`codeRunner.js`** | Client-side execution affordances for code blocks returned by the model. |

---

## 4. Model, Endpoint, and Configuration Modules

| Module | Responsibility |
|---|---|
| **`models.js`** | Model discovery / scanning, local model port probing, provider management, model selection UI state. |
| **`modelPicker.js`** | Composer model-picker dropdown and endpoint selection. |
| **`modelSort.js`** | Sorting helpers for model lists. |
| **`model/matchKey.js`** | Model-to-key matching helper. |
| **`providers.js`** | Provider metadata and account-management helpers. |
| **`providerDeviceFlow.js`** | OAuth device-flow support for providers. |
| **`presets.js`** | Character/preset selection, custom preset saving, inject prefix/suffix handling. |
| **`search.js`** | Web-search settings, provider selection, API key management. |
| **`settings.js`** | Settings panel (models, search, appearance, users, MCP, RAG, embedding, tokens). |
| **`admin.js`** | Admin panel and privileged user/endpoint configuration. |
| **`theme.js`** | Theme presets, custom colors, fonts, backgrounds, live theme switching. |

---

## 5. Session, Sidebar, and Workspace

| Module | Responsibility |
|---|---|
| **`sessions.js`** | Chat session list loading, creation, switching, renaming, archiving, library modal, and direct-chat creation. Tracks current session, streaming/research indicators in the sidebar. |
| **`workspace.js`** | Workspace folder path management for shell/file tool confinement. |
| **`search-chat.js`** | In-chat history search. |
| **`skills.js`** | Client-side skill library UI (load, edit, delete, test, and audit status display). |

---

## 6. Knowledge, Memory, and RAG

| Module | Responsibility |
|---|---|
| **`memory.js`** | AI memory CRUD, search/filter UI, memory extraction, count badge. |
| **`rag.js`** | Personal document RAG: load documents, add directories/files, show included paths. |
| **`group.js`** | Group-chat UI and model orchestration. |

---

## 7. Document and Editor Subsystems

| Module | Responsibility |
|---|---|
| **`document.js`** | Tabbed document editor, AI edit suggestions, Markdown/HTML/CSV editing, document streaming (`streamDocOpen`/`streamDocDelta`), and panel state. |
| **`documentLibrary.js`** | Document library modal. |
| **`editor/`** | Gallery image editor canvas modules: layers, brush, inpaint, crop, filters, state, history panel, top-bar wiring, canvas coordinate helpers, and AI model runners for inpainting/background-removal. |

---

## 8. Research UI

| Module | Responsibility |
|---|---|
| **`research/panel.js`** | Research panel UI, job list, and controls. |
| **`research/jobs.js`** | Research job polling and status rendering. |
| **`researchSynapse.js`** | Animated research-progress visualization shown inside the chat bubble during a research run. |

---

## 9. Gallery, Email, Calendar, Tasks, and Notes

| Module | Responsibility |
|---|---|
| **`gallery.js`** / **`galleryEditor.js`** | Gallery/image library and canvas editor entry points. |
| **`emailInbox.js`** / **`emailLibrary.js`** | Email inbox reader and library modal. Sub-modules handle signatures, reply recipients, state, and signature folding. |
| **`calendar.js`** / **`calendar/utils.js`** / **`calendar/reminders.js`** | Calendar views, event forms, reminders. |
| **`tasks.js`** | Scheduled task/recurring LLM job UI. |
| **`notes.js`** | Notes and todo panel, reminders, pinboard. |

---

## 10. Cookbook (Model Serving)

| Module | Responsibility |
|---|---|
| **`cookbook.js`** | Cookbook main UI: hardware fitting, presets, action panels. |
| **`cookbook-hwfit.js`** / **`cookbook-diagnosis.js`** / **`cookbook-deps-recipes.js`** | Hardware-fit scoring, dependency diagnosis, recipe handling. |
| **`cookbookDownload.js`** / **`cookbookServe.js`** / **`cookbookRunning.js`** / **`cookbookSchedule.js`** / **`cookbookPorts.js`** / **`cookbookProgressSignal.js`** | Model download/serve flow, running job cards, scheduling, port detection, and progress computation. |

---

## 11. Compare and Utility Modules

| Module | Responsibility |
|---|---|
| **`compare/index.js`** (with `compare/state.js`, `compare/stream.js`, `compare/panes.js`, `compare/selector.js`, `compare/scoreboard.js`, `compare/probe.js`, `compare/vote.js`, `compare/icons.js`) | Model compare mode: parallel streams, panes, scoring, vote UI. |
| **`censor.js`** | Text/image censor overlay toggles. |
| **`a11y.js`** | Accessibility helpers. |
| **`platform.js`** | Platform detection (macOS/Windows/Linux) and keyboard-modifier helpers. |
| **`escMenuStack.js`** | Stack manager for dismissible popups. |
| **`dragSort.js`** | Drag-to-sort shared behavior. |
| **`tourHints.js`** / **`tourAutoplay.js`** | Onboarding tour helpers. |
| **`color/hex.js`**, **`colorPicker.js`**, **`langIcons.js`**, **`util/ordinal.js`** | Small utility modules for color, language icons, and formatting. |

---

## 12. Frontend Event Streaming Flow

```
User submits composer
  └── chat.js::handleChatSubmit() builds FormData
        ├── fileHandler.uploadPending() for attachments
        ├── document.js saved (if a document panel is open)
        └── POST /api/chat_stream

Server responds with SSE stream
  └── chat.js reads chunks via res.body.getReader() + TextDecoder
        ├── Lines starting with "event:" set next-error state
        └── Lines starting with "data:" carry JSON payloads

JSON events are dispatched by "type":
  delta              → streamingRenderer → markdown → live reply text
  agent_prep         → update spinner label
  tool_start         → finalize text bubble; create agent-thread node with wave animation
  tool_progress      → append/update live stdout/stderr tail
  tool_output        → mark node done/failed, render output, diffs, screenshots
  agent_step         → finalize tool thread; create new msg-continuation bubble
  doc_stream_open    → document.js opens a live document
  doc_stream_delta   → document.js appends content to that document
  research_progress  → researchSynapse visualization + spinner timer
  research_sources   → build sources box for research
  research_done      → reload session history to show the report
  web_sources        → build web-search sources box
  model_info         → update role header with requested/actual model
  fallback           → show fallback model toast + update role label
  metrics            → collect/display token/cost metrics
  message_saved      → store database id on the message element
  budget_exceeded    → show budget banner
  rounds_exhausted   → show Continue button for step-limit hits
  teacher_takeover   → insert escalation banner, reset round state
  skill_saved        → show skill-learned banner
```

Foreground vs background streams:
- If the user switches sessions while a stream is running, `chat.js` pauses DOM
  updates and stores the state in `_backgroundStreams`. Completion is signaled
  with a sidebar dot/notifications, and the history is reloaded when the user
  returns.

---

## 13. What Changed from the Previous Summary

- The frontend is now exclusively ES6-module based; the old `<script>` tag load
  order is no longer authoritative.
- `chat.js` is the streaming controller, but message rendering has been split
  into `chatRenderer.js`, `streamingRenderer.js`, `chatStream.js`, and `researchSynapse.js`.
- New major subsystems added since the original summary: compare mode
  (`compare/`), document editor streaming (`document.js`), research UI
  (`research/`), model cookbook (`cookbook*.js`), group chat (`group.js`),
  voice/TTS (`voiceRecorder.js`, `tts-ai.js`), skill UI (`skills.js`), and
  slash autocomplete (`slashAutocomplete.js`).
- `sessions.js` now owns sidebar session state, streaming/research indicators,
  and the library/archive modals.
