# Module Organization Summary

## Purpose
This document describes what each JavaScript module is responsible for.

> **Note:** This file is a partial, historical overview — not a complete authoritative
> inventory. The authoritative module set is the current `static/js/` tree plus the
> scripts loaded by `static/index.html`. As of this writing that tree holds **65 `.js`
> files** across **8 subdirectories** (`calendar/`, `color/`, `compare/`, `editor/`,
> `emailLibrary/`, `markdown/`, `research/`, `util/`), and `static/index.html` loads
> **35** `/static…` script tags. The catalog below covers only the original core
> modules and is not kept in sync with every module.

---

## Core Modules (in static/js/)

### 1. **ui.js**
- UI helper functions and utilities
- Toast notifications (`showToast`, `showError`)
- Element getter (`el()`)
- Clipboard operations (`copyToClipboard`)
- Scroll management (`scrollHistory`, `setAutoScroll`)
- Auto-resize textarea
- Debounce utility

### 2. **markdown.js**
- Markdown processing and rendering
- Convert markdown to HTML (`mdToHtml`)
- Code block handling with syntax highlighting
- Content rendering for message arrays
- Text cleanup (`squashOutsideCode`)

### 3. **sessions.js**
- Session/chat management
- Create, load, delete, switch sessions
- Session history loading
- Direct chat creation with models
- Session renaming

### 4. **memory.js**
- AI memory management
- Load, add, edit, delete memories
- Memory search/filtering
- Memory UI rendering
- Memory count updates

### 5. **fileHandler.js**
- File attachment handling
- File picker dialog
- File upload to server
- Attachment strip rendering
- Pending files management
- File preview/removal

### 6. **voiceRecorder.js**
- Voice recording functionality
- Start/stop recording
- Audio file creation
- Microphone permission handling
- Recording UI updates

### 7. **models.js**
- Model scanning and display
- Local model discovery (ports 8000-8020)
- Provider management (OpenAI)
- Model selection UI

### 8. **rag.js**
- RAG (Retrieval Augmented Generation) management
- Load personal documents
- Add directories to RAG
- Display included files/directories

### 9. **presets.js**
- Conversation preset management
- Load, save, activate presets
- Custom preset configuration
- Temperature, tokens, system prompt settings

### 10. **search.js**
- Web search settings
- Provider selection (DuckDuckGo, Brave, SearXNG)
- API key management
- Save/load search configuration

### 11. **chat.js** ⭐ (The Big One)
- Main chat functionality
- Message handling (`addMessage`)
- Chat submission (`handleChatSubmit`)
- Streaming response handling
- Performance metrics display
- Abort request management
- Loading states and error handling

---

## Main Application File

### **app.js**
- Application initialization
- Event listener setup
- Drag & drop handlers
- Keyboard shortcuts
- Module initialization
- Global configuration (API_BASE)
- Coordinates all modules together

---

## Dependency Order (Load Order in HTML)
```html
<script src="/static/js/sessions.js"></script>      <!-- 1. Sessions first -->
<script src="/static/js/memory.js"></script>        <!-- 2. Memory -->
<script src="/static/js/markdown.js"></script>      <!-- 3. Markdown -->
<script src="/static/js/ui.js"></script>            <!-- 4. UI utilities -->
<script src="/static/js/fileHandler.js"></script>   <!-- 5. File handling -->
<script src="/static/js/voiceRecorder.js"></script> <!-- 6. Voice -->
<script src="/static/js/models.js"></script>        <!-- 7. Models -->
<script src="/static/js/rag.js"></script>           <!-- 8. RAG -->
<script src="/static/js/presets.js"></script>       <!-- 9. Presets -->
<script src="/static/js/search.js"></script>        <!-- 10. Search -->
<script src="/static/js/chat.js"></script>          <!-- 11. Chat -->
<script src="/static/app.js"></script>              <!-- 12. Main app LAST -->
