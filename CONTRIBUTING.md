
# Contributing to Odysseus

Thanks for helping! The project is moving quickly, so the best contributions are focused, easy to review, and easy to test.

## Before You Start

- **Search first:** Check existing issues and pull requests before opening a new one.
- **Stay focused:** Prefer one bug fix or feature per pull request.
- **Avoid noise:** Avoid broad rewrites, formatting-only changes, or moving many files unless the issue is specifically about structure.
- **Plan large features:** If you want to work on a large feature, open an issue first to discuss the approach.

## Development Setup

### Option 1: Docker (Recommended)

Docker is the safest path for normal testing and ensures environment consistency.

```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
cd odysseus
cp .env.example .env
docker compose up -d --build
```

### Option 2: Manual Python Setup

For manual development, use Python 3.11+.

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Option 3: Windows / WSL2

While native Windows is not actively tested, you can develop using **WSL2** (Ubuntu recommended):

1. Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install).
2. Clone the repo inside your WSL terminal (avoid `/mnt/c/` for performance).
3. Follow the "Manual Python Setup" steps above.
4. If using Docker, ensure the [Docker Desktop WSL2 backend](https://docs.docker.com/desktop/wsl/) is enabled.

## Running Checks

We recommend using the provided `Makefile` for common tasks if available:

```bash
make install   # Install dependencies
make test      # Run pytest
make lint      # Run py_compile checks
make run       # Start uvicorn server
```

If you prefer manual commands, run the smallest relevant checks for your change:

```bash
# Testing
python -m pytest tests/ -q

# Linting / Syntax Check
python -m py_compile app.py routes/*.py src/*.py

# Frontend Check (if applicable)
node --check static/js/<file-you-changed>.js
```

**Note on Test Isolation:**
When writing new tests, avoid importing `core.middleware` or `app` directly if possible. Prefer testing pure utility functions in isolation to prevent database startup side effects. See `tests/test_security_regressions.py` for examples of isolated unit tests.

For Docker-related changes:

```bash
docker compose config
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

*Mention what you ran in your pull request description. If you could not run a specific check, please state why.*

## Pull Requests

Good pull requests usually include:

- A short explanation of the bug or feature.
- The files or areas changed.
- Manual test steps or automated test results.
- Screenshots or short recordings for UI changes.
- Links to related issues (e.g., `Fixes #123`).

**Please keep PRs small.** Large PRs that mix unrelated cleanup, formatting, refactors, and behavior changes are much harder to review and may be delayed.

## Issue Reports

For bugs, please include:

- **Install method:** Docker, manual Python, WSL, etc.
- **Environment:** OS, browser, and device if relevant.
- **Reproduction:** Exact steps to reproduce the behavior.
- **Expectation:** Expected behavior vs. actual behavior.
- **Logs:** Relevant logs, screenshots, or terminal output.

For model-serving issues, include:

- **Backend:** Ollama, vLLM, SGLang, llama.cpp, LM Studio, etc.
- **Model:** Model name and version.
- **Hardware:** GPU/CPU and operating system.
- **Logs:** Cookbook task logs or server logs.

*Issues with only "help", "does not work", or a screenshot without context may be closed as not actionable.*

## Security

Do **not** post secrets, API keys, private logs, personal documents, or public IPs in issues or pull requests.

For security reports, please follow [SECURITY.md](SECURITY.md).
