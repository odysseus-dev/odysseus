# Axios Migration & Configurable Base Path - Documentation

## Overview

This document describes the refactoring of the Odysseus UI from native `fetch()` calls to Axios, with a configurable base path for reverse proxy support.

---

## Changes Summary

### 1. Axios Setup in `static/js/axios/api.js`

**Purpose:** Centralize HTTP client configuration with interceptors.

**What happens:**

- Axios is loaded globally from CDN (`index.html`) and configured with a base URL derived from `BASE_PATH`
- Response interceptor handles successful responses
- Error interceptor provides user-friendly error messages (following best practices)
- `apiFetch()` wraps native `fetch()` for SSE/streaming endpoints that need `ReadableStream`
- The configured `api` object is exported for use by other modules

---

### 2. HTML Template Updates

**Files modified:**

- `static/index.html`
- `static/login.html`

**What changes:**
All hardcoded paths are replaced with `{{BASE_PATH}}` template literals:

- `/api/...` → `{{BASE_PATH}}/api/...`
- `/static/...` → `{{BASE_PATH}}/static/...`
- `/` → `{{BASE_PATH}}/`

**Why:**
The `app.py` build process calls `_serve_html_with_nonce()` which performs string replacement:

```python
html = html.replace("{{CSP_NONCE}}", nonce)
html = html.replace("{{BASE_PATH}}", get_base_path())
```

This allows the same HTML to work both:

- When served directly from the app (BASE_PATH = `/`)
- When behind a reverse proxy (BASE_PATH = `/myapp/`)

---

### 3. JavaScript Module Refactoring

**Shared module:** `static/js/axios/api.js`

**Exports:**

```javascript
export const api = {
  get: axios.get,
  post: axios.post,
  put: axios.put,
  delete: axios.delete,
  // etc.
}
export { apiFetch, apiPath, apiErrorMessage, getBasePath }
```

**Migration pattern:**

```javascript
// Before
fetch('/api/chats')
  .then(r => r.json())
  .catch(e => console.error(e))

// After
import { api } from './axios/api.js'
api.get('/api/chats')
  .then(r => r.data)
  .catch(e => {
    console.error(e.response?.data?.error?.message || e.message)
  })
```

**Streaming endpoints** (`/api/chat_stream`, `/api/chat/resume/`*, `/api/rewrite`, etc.) should use `apiFetch()` instead of axios so the browser can read `response.body` as a stream.

---

### 3a. Migration Progress (`static/js`)

Track module-by-module migration from `fetch()` / `API_BASE` to `api` / `apiPath` / `apiFetch`.

**Infrastructure**

- [x] `static/login.html` (inline scripts)
- [x] `static/index.html` (inline scripts)
- [x] `static/js/axios/api.js` — shared HTTP client
- [x] `static/js/chat.js`
- [ ] `static/js/admin.js`
- [ ] `static/js/assistant.js`
- [ ] `static/js/calendar.js`
- [ ] `static/js/calendar/reminders.js`
- [ ] `static/js/censor.js`
- [ ] `static/js/chatRenderer.js`
- [ ] `static/js/codeRunner.js`
- [ ] `static/js/compare/index.js`
- [ ] `static/js/compare/models.js`
- [ ] `static/js/compare/panes.js`
- [ ] `static/js/compare/probe.js`
- [ ] `static/js/compare/selector.js`
- [ ] `static/js/compare/state.js`
- [ ] `static/js/compare/stream.js`
- [ ] `static/js/compare/vote.js`
- [ ] `static/js/cookbook-diagnosis.js`
- [ ] `static/js/cookbook-hwfit.js`
- [ ] `static/js/cookbook.js`
- [ ] `static/js/cookbookDownload.js`
- [ ] `static/js/cookbookRunning.js`
- [ ] `static/js/cookbookSchedule.js`
- [ ] `static/js/cookbookServe.js`
- [ ] `static/js/document.js`
- [ ] `static/js/documentLibrary.js`
- [ ] `static/js/editor/ai-inpaint.js`
- [ ] `static/js/editor/ai-models.js`
- [ ] `static/js/editor/ai-tool-runner.js`
- [ ] `static/js/editor/ai-tools-misc.js`
- [ ] `static/js/editor/wire-import.js`
- [ ] `static/js/emailInbox.js`
- [ ] `static/js/emailLibrary.js`
- [ ] `static/js/fileHandler.js`
- [ ] `static/js/gallery.js`
- [ ] `static/js/galleryEditor.js`
- [ ] `static/js/group.js`
- [x] `static/js/init.js`
- [ ] `static/js/keyboard-shortcuts.js`
- [ ] `static/js/markdown.js`
- [ ] `static/js/memory.js`
- [ ] `static/js/modelPicker.js`
- [ ] `static/js/models.js`
- [ ] `static/js/notes.js`
- [ ] `static/js/presets.js`
- [ ] `static/js/rag.js`
- [ ] `static/js/research/jobs.js`
- [ ] `static/js/research/panel.js`
- [ ] `static/js/search-chat.js`
- [ ] `static/js/search.js`
- [ ] `static/js/sessions.js`
- [ ] `static/js/settings.js`
- [ ] `static/js/signature.js`
- [ ] `static/js/skills.js`
- [ ] `static/js/slashAutocomplete.js`
- [ ] `static/js/slashCommands.js`
- [ ] `static/js/tasks.js`
- [ ] `static/js/theme.js`
- [x] `static/js/tts-ai.js`
- [ ] `static/js/voiceRecorder.js`
- [ ] `static/js/workspace.js`
- [x] `static/app.js`

---

### 5. Environment Configuration

**File:** `.env`

**New variable:**

```
BASE_PATH=/
```

**Usage:**

- Default: `/` (no reverse proxy)
- Behind Nginx/Apache: `BASE_PATH=/odysseus/`
- Behind Traefik: `BASE_PATH=/myapp/`

The value is injected at build time via template replacement.

---

## Build Process

The `app.py` file's `_serve_html_with_nonce()` function handles template rendering:

```python
def _serve_html_with_nonce(html: str, nonce: str) -> str:
    html = html.replace("{{CSP_NONCE}}", nonce)
    html = html.replace("{{BASE_PATH}}", get_base_path())
    return html
```

This ensures all HTML templates are rendered with the correct base path before being served.

---

## Verification

After changes, verify:

1. App runs normally with `BASE_PATH=/`
2. App runs correctly when deployed behind reverse proxy with custom BASE_PATH
3. All API calls use the configured base path
4. Error messages are user-friendly
5. No console errors related to path mismatches

---

## Notes

- The `API_BASE` variable found in some JS files should be replaced with `apiPath()` / `getBasePath()` from `static/js/axios/api.js`
- Cookie-based authentication remains unchanged (no migration needed)
- All static assets continue to be served correctly

