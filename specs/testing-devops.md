# Testing And Devops

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers development and validation surfaces in:

- `tests/`, `tests/conftest.py`, `tests/*.mjs`, and `tests/bombadil-spec.ts`;
- `pyproject.toml`;
- `requirements.txt` and `requirements-optional.txt`;
- `package.json` and `package-lock.json`;
- `Dockerfile`, `docker-compose.yml`, `docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`, top-level standalone GPU compose files, and `docker/entrypoint.sh`;
- `scripts/`, `scripts/odysseus`, `scripts/_lib/cli.py`, `scripts/_completion/*`, `scripts/pr_blocker_audit.py`, and `scripts/odysseus-*`;
- GPU helper scripts `scripts/check-docker-gpu.sh` and `scripts/check-docker-amd-gpu.sh`;
- `.github/` templates, workflows, and description-check scripts;
- contributor workflow docs in `CONTRIBUTING.md` and `docs/pr-blocker-audit.md`;
- platform launchers `launch-windows.ps1`, `start-macos.sh`, `build-macos-app.sh`, and `update_windows.bat`;
- setup/service files such as `setup.py`, `install-service.sh`, and `odysseus-ui.service`.

## Test Runtime

Pytest is configured in `pyproject.toml` with:

- `testpaths = ["tests"]`;
- `asyncio_mode = "auto"`.

The expected local command uses the project venv:

```bash
./venv/bin/pytest <test path>
```

Activated-venv `python -m pytest <test path>` is equivalent. System/global `pytest` is not authoritative for this repo because installed versus stubbed dependencies can change collection behavior.

`tests/conftest.py` inserts the repo root on `sys.path` and conditionally stubs missing heavy/runtime dependencies such as SQLAlchemy, FastAPI, Starlette, Pydantic, httpx, bcrypt, and pyotp. Tests that need real dependencies use explicit imports/skips. Tests that stub `sys.modules`, environment variables, globals, or parent packages must restore them with `monkeypatch` or an equivalent cleanup pattern.

Focused regression tests are preferred for narrow behavior changes. Broaden tests when touching shared contracts such as auth, owner filtering, tool output, context building, provider calls, persistence, frontend rendering, or route/API shapes.

## JS And UI Tests

The repo has no frontend build pipeline, npm test script, or type-check script. `package.json` owns Node dependencies for Bombadil and the Anthropic SDK, and `package-lock.json` owns npm integrity/version state.

Current frontend/JS validation includes:

- pytest wrappers that run Node snippets and usually skip when `node` is missing;
- direct `.mjs` regressions under `tests/`;
- `tests/bombadil-spec.ts`, which requires npm-installed Bombadil dev dependencies and a running/browser-capable UI workflow when used.

Use `node --check static/js/<changed-file>.js` for syntax checks on changed JS files when applicable. This is not a full module-graph, browser-global, or DOM integration check.

## Dependencies

`requirements.txt` owns core runtime and test dependencies, including pytest, pytest-asyncio, MCP, Chroma HTTP client, fastembed, qrcode, and core parsing/search/calendar dependencies.

`requirements-optional.txt` owns optional feature dependencies:

- `faster-whisper` for local STT;
- `duckduckgo-search` for DDG library support, while provider code can fall back to HTML scraping;
- `PyMuPDF` for PDF forms/rendering with AGPL implications for a network-served app;
- `markitdown[docx,pptx,xlsx,xls]` for Office/EPUB extraction, pinned to a release older than 30 days.

Optional dependencies should produce clear degraded behavior when absent unless intentionally promoted to core. MarkItDown and PyMuPDF already have focused degraded-path coverage; local STT missing-`faster-whisper` behavior is a remaining coverage gap.

Chroma has two compatibility modes:

- Docker uses a separate `chromadb` service and core `chromadb-client`/`fastembed`;
- native macOS setup removes conflicting `chromadb-client` and installs full `chromadb`.

Vector features should fail fast or degrade to unhealthy/keyword fallback when the service is unavailable.

## Docker Runtime

Docker Compose is the primary deployment path:

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=120 odysseus
```

`docker-compose.yml` starts Odysseus, ChromaDB, SearXNG, and ntfy. It binds services to loopback by default, persists `data/`, `logs/`, SSH identity, HuggingFace cache, and user-local Python installs, and gives the Odysseus container host-loopback reachability through `host.docker.internal`.

`Dockerfile` builds a Python 3.12 slim image with Node/npm, tmux, OpenSSH client, git/cmake, and `gosu`.

`docker/entrypoint.sh` owns writable path ownership repair, PUID/PGID privilege drop, vLLM/CUDA environment defaults, idempotent `setup.py`, and final uvicorn execution.

Docker does not mount the host Docker socket by default. Mounting it would grant powerful host access and is outside the default trust boundary.

## GPU And Platform

Base `docker-compose.yml` plus `docker/gpu.nvidia.yml` or `docker/gpu.amd.yml` are the GPU source of truth. Top-level `docker-compose.gpu-nvidia.yml` and `docker-compose.gpu-amd.yml` are standalone mirrors for stack-management UIs that accept one compose file. `tests/test_gpu_compose_standalone.py` guards drift between those forms.

GPU overlays pass host devices/runtime flags only. They do not install CUDA/ROCm userspace or serving engines; those are installed later through Cookbook/dependency flows.

NVIDIA helper behavior:

- `scripts/check-docker-gpu.sh` diagnoses passthrough;
- it is read-only by default;
- toolkit install and `.env` edits require explicit user flags and successful passthrough checks.

AMD helper behavior:

- `scripts/check-docker-amd-gpu.sh` is read-only;
- it prints expected `COMPOSE_FILE`/`RENDER_GID` values and verifies `/dev/kfd`/`/dev/dri` visibility.

Native platform launchers:

- `launch-windows.ps1` requires Python 3.11+, creates `venv`, installs `requirements.txt`, runs `setup.py`, warns when Git Bash is missing, and starts uvicorn on port 7000 by default.
- `start-macos.sh` reads `.env`, defaults to port 7860 to avoid AirPlay conflicts, prefers Homebrew arm64 Python, installs/tolerates Homebrew Cookbook deps, handles Chroma package conflicts, runs `setup.py`, and starts uvicorn.
- `build-macos-app.sh` builds a launcher app around the existing repo venv and logs to `logs/odysseus-app.log`.
- `update_windows.bat` owns the tested Windows Docker update flow.

## Scripts And CLI

`scripts/odysseus` is the umbrella dispatcher for executable `scripts/odysseus-*` commands. It discovers subcommands and executes them through the project venv Python when available.

`scripts/_lib/cli.py` owns shared CLI behavior:

- repo-root importability;
- quiet logging;
- JSON output and `--pretty`;
- `--version`;
- common parser scaffolding;
- exit handling.

Shell completions in `scripts/_completion/` introspect CLI `--help` output through the venv and cache subcommands.

`scripts/odysseus-*` provide local CLI surfaces for backup, calendar, contacts, Cookbook, docs, gallery, logs, mail, MCP, memory, notes, personal docs, presets, research, sessions, signatures, skills, tasks, theme, and webhooks.

When route/API behavior changes, check whether a matching CLI script depends on the old shape. There is no central CLI scrubber: each credential/log/mail/task/backup/MCP/webhook script owns its own sensitive-output behavior.

## GitHub Metadata

`.github/` owns issue/PR templates, description-check workflows, and a lightweight CI workflow. Current CI compiles Python with `python -m compileall`, syntax-checks first-party JS with `node --check`, and runs `python -m pytest -q` as an informational/non-blocking job; the pytest job skips documentation-only changes.

`CONTRIBUTING.md` owns the branch model: PRs target `dev`; `main` is the curated user-running branch fast-forwarded from stable `dev` commits. Contributors who accidentally target `main` should retarget the PR base without rebasing.

PR description checks:

- run on `pull_request_target`;
- check out only base-branch `.github/scripts`;
- skip bot PRs;
- require Summary, Linked Issue, Type of Change, duplicate-search checklist, and substantive How to Test content;
- update a bot comment and swap `ready for review` / `needs work` labels.

Issue description checks:

- validate bug or feature sections based on labels;
- flag unfilled dropdown placeholders such as `-- Please Select --`;
- route public vulnerability reports toward GitHub Security Advisories;
- update a bot comment and swap status labels.

`scripts/pr_blocker_audit.py` is a read-only maintainer/contributor triage helper documented in `docs/pr-blocker-audit.md`. It can fetch or ingest open PR metadata, estimate hot files and possible duplicate groups, and emit Markdown, JSON, or terminal reports. Its duplicate/blocker output is advisory, not an authority that a PR is blocked.

Before posting PRs or issues, compare drafts against current templates on latest `main` or current `dev` as appropriate for the target. Keep unpublished drafts and raw related-search exports out of tracked implementation specs unless intentionally promoted.

## Artifacts And Secrets

- Do not read `.env*` files unless a user explicitly asks for a controlled setup/debug step; never print their values.
- Backup files, logs, CLI JSON, and raw issue/PR search exports can contain sensitive local data.
- Do not commit raw GitHub JSON unless there is an explicit maintainer reason. Prefer compact Markdown reports when publishing analysis.
- Specs are implementation truth. Planning, research, branch notes, and draft reports belong in tracked project docs when promoted.

## Development Checks

Common local checks:

```bash
./venv/bin/pytest tests/path.py::test_name
python -m py_compile app.py routes/*.py src/*.py
node --check static/js/changed-file.js
docker compose config
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

Run the app for user-facing or integration changes. Unit tests and syntax checks do not replace end-to-end verification for UI, Docker, provider, auth, or routing behavior.

## Shared Test Helpers

`tests/helpers/` owns reusable test scaffolding. `cli_loader.load_script()` loads CLI files without running their `main()` entrypoint. `db_stubs` owns small DB stand-ins for tests that should not import a real app database. `import_state` owns conservative `sys.modules` and parent-module-attribute restoration for tests that install fake modules or import route files under alternate stubs. `tests/README.md` documents helper conventions and review expectations.

## Current Gaps

- Fresh install smoke coverage across Linux native, Docker, macOS native/app, Windows native, WSL/Git Bash, missing Node/npm, missing Chroma service, and GPU overlays remains a roadmap item.
- There is no frontend build/type-check/npm test pipeline.
- CI now covers Python compile, first-party JS syntax, and pytest smoke; it does not cover Docker compose validation, launcher smoke tests, browser/module-graph execution, or platform installs.
- Optional dependency behavior is broad; remaining gaps include local STT missing-`faster-whisper` behavior and provider combinations not covered by focused tests.
- GitHub description-check scripts and `scripts/pr_blocker_audit.py` need continued local fixtures for section parsing, placeholder stripping, label swaps, workflow-safe behavior, and duplicate/hot-file heuristics.
- Spec bootstrap rules lack meta tests for reading `_readme.md`, spec shape, `.env*` handling, draft/report placement, and shared helper conventions.
- NVIDIA helper install/`.env` mutation paths and real Docker/GPU startup are not covered by local tests.
- Bash/Zsh completion behavior is not covered.
- There is no canonical full-suite known-failing/flaky ledger.
- There is no central CLI redaction/sensitive-output regression matrix across backup, logs, mail, MCP, tasks, and webhook scripts.
- Dependency/image pinning policy is mixed: Python requirements are mostly unpinned, SearXNG is pinned, Chroma image currently uses `latest`, npm uses a lockfile, and browser MCP uses cache-gated `@playwright/mcp@latest`.
