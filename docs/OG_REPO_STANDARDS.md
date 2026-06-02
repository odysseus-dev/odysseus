# Odysseus Original Repository Standards & Roadmap Alignment

This document codifies the original repository standards, testing practices, and roadmap targets found in the official `CONTRIBUTING.md` and `ROADMAP.md` files of **Odysseus**, and outlines how our fork's contributions align with them.

---

## 📋 1. Original Repository Standards

As outlined in `CONTRIBUTING.md`, the Odysseus project moves quickly, so contributions must adhere to the following guidelines to ensure they are easy to review, test, and merge:

### Focus & Scope
* **Prefer single-purpose PRs**: One bug fix or feature per pull request.
* **Avoid broad rewrites**: Do not perform formatting-only changes or move many files unless the issue is specifically about repository structure.
* **Keep PRs small**: Large PRs that mix unrelated cleanup, formatting, refactors, and behavior changes are much harder to review and are discouraged.

### Security Hygiene
* **No Secrets**: Do not post secrets, API keys, private logs, personal documents, or public IPs in issues or pull requests.
* **Security Reports**: Follow the guidelines in `SECURITY.md` for reporting vulnerabilities.

---

## 🧪 2. Testing & Verification Standards

Before submitting any pull request, developers are expected to run the smallest relevant checks for their changes:

### Python Backend Checks
* **Run automated tests**:
  ```bash
  python -m pytest
  ```
* **Verify Python compilation**:
  ```bash
  python -m py_compile app.py routes/*.py src/*.py
  ```

### Frontend JavaScript Checks
* **Syntax check modified JS files**:
  ```bash
  node --check static/js/<file-you-changed>.js
  ```

### Docker Verification (If Applicable)
* **Verify compose configuration**:
  ```bash
  docker compose config
  ```
* **Build and run containers**:
  ```bash
  docker compose up -d --build
  ```
* **Inspect container logs**:
  ```bash
  docker compose logs --tail=120 odysseus
  ```

---

## 🗺️ 3. Roadmap Targets & Fork Alignment

Our fork's contributions are directly aligned with several high-priority refactor targets and frontend goals listed in `ROADMAP.md`:

### 🎯 Target 1: Accessibility Pass
* **Roadmap Goal**: *"Accessibility pass: keyboard navigation, focus states, contrast, reduced motion."*
* **Our Alignment**: We designed our onboarding UX improvements (such as `.setup-trigger-link` and `.use-code` buttons) using semantic HTML and standard CSS classes, ensuring they are fully keyboard-navigable and respect focus-visible states without relying on fragile inline styles.

### 🎯 Target 2: Tighten First-Run Setup & Tours
* **Roadmap Goal**: *"Tighten first-run setup, hints, and tours so they do not repeat or fight each other."*
* **Our Alignment**: Our tour dropdown fixes directly resolve race conditions where floating dropdowns (like the Model Picker or Overflow Menu) remained open and overlapped highlights during tour transitions. By exposing global close handlers and calling them in `before()` hooks, we ensured that tours and setup hints do not fight each other.

### 🎯 Target 3: CSS Cleanup
* **Roadmap Goal**: *"CSS cleanup. `static/style.css` basically Calypso's island atm."*
* **Our Alignment**: We avoided adding to "Calypso's island" by refactoring our initial inline styles into clean, isolated CSS rules in `static/style.css`. We grouped our custom styles under descriptive classes (`.use-code`, `.setup-trigger-link`, `.setup-clickable-provider`, `.setup-clickable-code`) at the bottom of the stylesheet to keep them modular and easy to refactor.

### 🎯 Target 4: Tour Core Helper
* **Roadmap Goal**: *"Tour core helper. The onboarding tours have too much copy-pasted scaffolding; promote a shared `tour-core.js` helper before adding more tours."*
* **Our Alignment**: We acknowledged this scaffolding target by keeping our tour steps as clean and declarative as possible. By exposing global close handlers (`window.closeModelPicker`, `window.closeOverflowMenu`), we made it extremely easy for a future `tour-core.js` helper to automatically hook into and dismiss open dropdowns/menus during transitions.
