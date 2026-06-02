# Odysseus Fork Documentation & Development Flow

> **Status:** active
> **Owner:** greyZ
> **Fork Remote:** `https://github.com/k-dot-greyz/odysseus.git`
> **Upstream Remote:** `https://github.com/pewdiepie-archdaemon/odysseus.git`

---

## 🧭 1. Repository Housekeeping & Location

### Standard Path
Odysseus is cloned as a local submodule/repository under:
```
dex/09-repos/odysseus/
```

### Git Isolation (No Tracking Noise)
To keep the parent monorepo's `git status` perfectly clean and prevent manually cloned sub-repositories from showing up as untracked folders (or accidental gitlinks), always add the paths to the top-level `.gitignore` file under the `# zenOS Specific` section:
```gitignore
# zenOS Specific
dex/09-repos/odysseus/
dex/09-repos/heretic/
```

---

## 🛠️ 2. Environment Setup & Installation

Odysseus runs on Python 3.11+ (successfully verified on **Python 3.14.5** on Apple Silicon).

### Step-by-Step Setup
1. **Navigate to the repository**:
   ```bash
   cd dex/09-repos/odysseus
   ```
2. **Create a local virtual environment**:
   ```bash
   python3 -m venv venv
   ```
3. **Upgrade pip & install dependencies**:
   ```bash
   venv/bin/pip install --upgrade pip
   venv/bin/pip install -r requirements.txt
   ```
4. **Run the first-time setup script**:
   ```bash
   venv/bin/python setup.py
   ```
   *Note: This creates data directories, initializes the SQLite database, and prints a randomized secure temporary admin password (e.g., `7C2CfqudAwhPIFRrgy1k5eKt`).*

---

## ⚡ 3. Runtime & Port Configuration

### ⚠️ The Port 7000 Conflict (macOS Monterey+)
* **The Problem**: By default, Odysseus attempts to bind to port `7000`. On macOS Monterey (macOS 12) and later, the **AirPlay Receiver / AirTunes** system daemon listens on port `7000` by default.
* **The Symptom**: Starting Uvicorn on port `7000` results in `[Errno 48] address already in use` crashes, or `curl` requests to `localhost:7000` return a `403 Forbidden` from AirTunes instead of hitting Odysseus.
* **The Fix**: **Always run Odysseus on port `7070`** (or another safe port like `7001`) on macOS:
  ```bash
  --port 7070
  ```

### 🏃 Running in the Background (Sandbox-Safe)
When executing background servers inside the Cursor IDE sandbox, using `block_until_ms: 0` can fail due to `/bin/zsh` terminal spawn errors inside the container.
* **The Standard**: Run the server using shell-native backgrounding with output redirection:
  ```bash
  venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 7070 >logs/uvicorn.log 2>&1 &
  ```
* **Monitoring**: Verify startup and view runtime logs by reading the output file:
  ```bash
  tail -n 50 logs/uvicorn.log
  ```

---

## 🎨 4. Onboarding UX Architecture

We implemented a zero-friction, highly interactive onboarding experience for first-time users setting up their API keys.

### Clickable Setup Triggers
* **Element**: `.setup-trigger-link`
* **Behavior**: Clicking the `/setup` text on the welcome screen or fallback state automatically fills `/setup` into the chat input field (`#message`) and submits the form, instantly launching the setup guide.

### "Use in Chat" Interactive Buttons
* **Element**: `.use-code`
* **Behavior**: Typewriter-generated code blocks (e.g., `deepseek sk-...`) render with a down-arrow "Use in Chat" button next to the copy button. Clicking it pre-populates the chat input, focuses the cursor right after the prefix, and trims any trailing `...` placeholders.

### Intelligent Provider Prefixing & Console Links
* **Dynamic Prefixes**: Instead of assuming `sk-` for all keys, we map provider names to their correct prefix (e.g., `AQ.` for Gemini, `gsk_` for Groq, `sk-ant-` for Anthropic) and auto-detect pasted Gemini keys starting with `AQ.`.
* **Direct Console Links** (`.setup-clickable-provider`): Clicking on a provider name in the setup guide automatically opens their official API key creation console in a new browser tab (e.g., Google AI Studio, Anthropic Console, OpenAI Platform) while pre-populating the chat input with the correct prefix.

### Style Isolation (No Inline Styles)
* **Rule**: To respect Odysseus's styling conventions and maintain clean HTML, **never use inline styles** for custom UX components. Always add rules to `static/style.css`.

---

## 🎛️ 5. Tour Dropdown Race Condition Fixes

Floating dropdown menus (like the Model Picker or the Overflow Menu) can remain open and overlap highlights during onboarding tour transitions.

### Exposing Close Handlers Globally
We expose internal close functions on the global `window` object to allow the tour controller to programmatically dismiss them:
* **Overflow Menu**: `window.closeOverflowMenu` in `static/app.js`
* **Model Picker**: `window.closeModelPicker` in `static/js/modelPicker.js`

### Tour Step Hooks
In `static/js/slashCommands.js`, we use `before()` hooks on tour steps to dismiss open menus before highlighting the next element:
```javascript
{ sel: '#mode-agent-btn', text: '...', mode: 'click',
  before() {
    if (typeof window.closeModelPicker === 'function') {
      window.closeModelPicker();
    } else {
      document.getElementById('model-picker-menu')?.classList.add('hidden');
    }
  } }
```

### Tour Cleanup
Always call both close handlers inside `_clearTour()` to ensure that if a user skips, cancels, or completes the tour, the UI is left perfectly clean:
```javascript
const _clearTour = () => {
  // ...
  if (typeof window.closeOverflowMenu === 'function') window.closeOverflowMenu();
  if (typeof window.closeModelPicker === 'function') window.closeModelPicker();
};
```

---

## 🐙 6. Git & Pull Request Workflow

### Fork Configuration
The repository is configured with two remotes:
* `upstream`: `https://github.com/pewdiepie-archdaemon/odysseus.git` (Official repository)
* `origin`: `https://github.com/k-dot-greyz/odysseus.git` (Your personal fork)

### Safe PR Update Strategy
* **Rule**: **NEVER force-push to upstream `main`.**
* **PR Updates**: To update an existing open pull request on the upstream repository, append your new commits to your local feature branch (`feat/onboarding-ux-improvements`) and push standardly:
  ```bash
  git push origin HEAD
  ```
  GitHub will automatically detect the new commits and update the open PR on the upstream repository.
