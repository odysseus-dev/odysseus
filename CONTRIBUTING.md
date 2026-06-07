# Contributing to Odysseus

Thanks for helping. The project is moving quickly, so the best contributions are focused, easy to review, and easy to test.

## Branch model

Odysseus has two branches:

- **`dev`** — where all PRs land. Things can be in flux here; the merge button gets used freely.
- **`main`** — what users run. Curated and tested by the maintainer. Fast-forwarded to a stable `dev` commit at each release.

**Open your PR against `dev`, not `main`.** The GitHub "base" dropdown defaults to `dev`. If you opened a PR against `main` by accident, click "Edit" on the PR and change the base — no rebase needed.

End-users cloning the repo will land on `dev` by default. To run the curated/stable version: `git checkout main` after clone.

## Before You Start

- Search existing issues and pull requests before opening a new one.
- Prefer one bug fix or feature per pull request.
- Avoid broad rewrites, formatting-only changes, or moving many files unless the issue is specifically about structure.
- If you want to work on a large feature, open an issue first and describe the approach.

## Setup

Docker is the recommended path for normal testing:

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

Manual development uses Python 3.11+:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 7000
```

Windows is not actively tested. Docker on Linux or a Linux/macOS manual install is the safer path for now.

## Running Checks

Run the smallest relevant checks for your change:

```bash
python -m pytest
python -m py_compile app.py routes/*.py src/*.py
node --check static/js/<file-you-changed>.js
```

For Docker-related changes:

```bash
docker compose config
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

Mention what you ran in the pull request description. If you could not run a check, say so.

## Pull Requests

Good pull requests usually include:

- A short explanation of the bug or feature.
- The files or areas changed.
- Manual test steps or automated test results from running the actual app, not just the test suite.
- Screenshots or short recordings for UI changes.
- Links to related issues, for example `Fixes #123`.

Please keep PRs small. Large PRs that mix unrelated cleanup, formatting, refactors, and behavior changes are much harder to review.

> **Auto-generated PRs.** If you are running an LLM agent (Devin, Cursor, OpenHands, Claude Code, etc.) against this repo: please open an issue describing the problem first instead of opening a PR directly. Bulk agent-generated PRs that don't match the project's visual style or contribution format will be closed without review, even when the underlying fix is correct.

## Style and visual changes

Odysseus has an intentional visual style. PRs that ignore it will be closed without merge, no matter how correct the underlying code is.

Before submitting any change that affects what the app looks like — buttons, icons, fonts, colors, spacing, layout, CSS, HTML, SVG, or any `static/js/` module that draws to the DOM — please:

1. **Run the app locally** and view the change in a browser. Type-checks and unit tests are not enough.
2. **Attach a screenshot or short clip** of the change in the running app. Add a mobile screenshot too if the change affects mobile.
3. **Match the existing visual language.** Specifically:
   - Reuse existing CSS variables (`--red`, `--fg`, `--bg`, `--card`, `--border`, …). Do not introduce new color values, font sizes, or spacing units.
   - Reuse existing button, input, card, and border classes. Don't invent parallel styling for similar widgets.
   - **No Unicode emoji in UI or code.** Use inline SVG (matching the monochrome icon style already in `static/index.html`) or plain text.
   - Monospaced font (`Fira Code`) for primary UI text. Don't override.
   - Dark theme is the default; any light-mode work goes through the existing theme system, not hard-coded.
4. **Don't add parallel components.** If a similar widget already exists in the app, extend it instead of writing a new one.

If you are unsure whether a change is "visual," it is. Default to attaching a screenshot.

## Code conventions

Don't hardcode values that the project already exposes through a constant or a helper. Hardcoded literals drift out of sync, break on non-default deployments, and reintroduce bugs we've already fixed.

- **Filesystem paths:** never build writable paths from `Path(__file__)...` into the source tree or hardcode `/app/...`. Use `DATA_DIR` (and the other path constants) from `core.constants`, e.g. `Path(DATA_DIR) / "logs" / "x.log"`. The source tree is read-only in Docker, and `/app/...` does not exist on native runs. Guard directory creation so an unwritable path degrades gracefully instead of crashing at import.
- **Internal API / loopback URLs:** don't hardcode `http://localhost:7000`. Use `internal_api_base()` from `core.constants` (it honors `ODYSSEUS_INTERNAL_BASE` / `APP_PORT`).
- **Ports, limits, model lists, and similar:** reuse the existing constant if one exists; if it doesn't and the value is used in more than one place, add a constant rather than copying the literal.

If you need a value that has no constant or helper yet, add one in the appropriate module (usually `core/constants.py` or `src/constants.py`) and import it, rather than repeating a literal across files.

## Issue Reports

For bugs, include:

- Install method: Docker, manual Python, WSL, etc.
- OS, browser, and device if relevant.
- Exact steps to reproduce.
- Expected behavior and actual behavior.
- Logs, screenshots, or terminal output.

For model-serving issues, include:

- Backend: Ollama, vLLM, SGLang, llama.cpp, LM Studio, etc.
- Model name.
- GPU/CPU and operating system.
- Cookbook task logs or server logs.

Issues with only "help", "does not work", or a screenshot without context may be closed as not actionable.

## Security

Do not post secrets, API keys, private logs, personal documents, or public IPs in issues or pull requests.

For security reports, follow [SECURITY.md](SECURITY.md).

