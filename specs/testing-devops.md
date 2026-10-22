# Testing And Devops

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers development and validation surfaces in:

- `tests/`, `tests/conftest.py`, `tests/*.mjs`, and `tests/bombadil-spec.ts`;
- `tests/run_focus.py`, `tests/run_order_report.py`, `tests/_taxonomy.py`, `tests/TESTING_STANDARD.md`, and `tests/LAYOUT_INVENTORY.md`;
- `pyproject.toml`;
- `requirements.txt` and `requirements-optional.txt`;
- `package.json` and `package-lock.json`;
- `Dockerfile`, `docker-compose.yml`, `docker/gpu.nvidia.yml`, `docker/gpu.amd.yml`, `docker/host-docker.yml`, top-level standalone GPU compose files, and `docker/entrypoint.sh`;
- `scripts/`, `scripts/odysseus`, `scripts/_lib/cli.py`, `scripts/_completion/*`, `scripts/pr_blocker_audit.py`, and `scripts/odysseus-*`;
- GPU helper scripts `scripts/check-docker-gpu.sh` and `scripts/check-docker-amd-gpu.sh`;
- `.github/` templates, workflows, and description-check scripts;
- contributor workflow docs in `CONTRIBUTING.md` and `website/pr-blocker-audit.md`;
- platform launchers `launch-windows.ps1`, `launcher.py`, `Odysseus.spec`, `build-windows-portable.ps1`, `start-macos.sh`, `build-macos-app.sh`, and `update_windows.bat`;
- setup/service files such as `setup.py`, `install-service.sh`, and `odysseus-ui.service`.

## Test Runtime

Pytest is configured in `pyproject.toml` with:

- `testpaths = ["tests"]`;
- `asyncio_mode = "auto"`;
- marker and fast-lane/duration-reporting settings used by focused test runs.

The expected local command uses the project venv:

```bash
./venv/bin/pytest <test path>
```

Activated-venv `python -m pytest <test path>` is equivalent. System/global `pytest` is not authoritative for this repo because installed versus stubbed dependencies can change collection behavior.

`tests/conftest.py` inserts the repo root on `sys.path` and conditionally stubs missing heavy/runtime dependencies such as SQLAlchemy, FastAPI, Starlette, Pydantic, httpx, bcrypt, and pyotp. Tests that need real dependencies use explicit imports/skips. Tests that stub `sys.modules`, environment variables, globals, or parent packages must restore them with `monkeypatch` or an equivalent cleanup pattern.

The suite currently contains a growing set of `test_*.py` files. Treat that count as a moving source metric, not a target; focused regression tests are still preferred for narrow changes.

Focused regression tests are preferred for narrow behavior changes. Broaden tests when touching shared contracts such as auth, owner filtering, OAuth/token custody, tool output, context building, provider calls, persistence, frontend rendering, or route/API shapes.

`tests/run_focus.py` and `tests/_taxonomy.py` provide a local focused-run helper and category map. `.github/scripts/focused_test_guidance.py` maps changed files to suggested focused tests for PR review, while the configured full pytest CI job is authoritative. `tests/TESTING_STANDARD.md` documents expectations for targeted validation, and `tests/LAYOUT_INVENTORY.md` records the test-suite layout. CLI tests live under `tests/cli/`.

## JS And UI Tests

`frontend/modules.json` maps canonical `frontend/src/` modules to committed outputs under `static/`. `scripts/build_frontend.py --check` verifies output freshness/publication attributes; `scripts/check_frontend.mjs` checks architecture/import boundaries. Root `package.json` owns Bombadil, Playwright and Acorn plus photo-editor browser-test scripts; the lockfile owns npm integrity/version state. This is an incremental source migration rather than a bundler-wide conversion.

Current frontend/JS validation includes:

- pytest wrappers that run Node snippets and usually skip when `node` is missing;
- direct `.mjs` regressions under `tests/`;
- `tests/bombadil-spec.ts`, which requires npm-installed Bombadil dev dependencies and a running/browser-capable UI workflow when used.

Use `node --check static/js/<changed-file>.js` for syntax checks on changed JS files when applicable. This is not a full module-graph, browser-global, or DOM integration check.

## Dependencies

`requirements.txt` owns core runtime and test dependencies, including `psycopg2-binary`, pytest, pytest-asyncio, MCP, Chroma HTTP client, fastembed, qrcode, and core parsing/search/calendar dependencies.

`requirements-optional.txt` owns optional feature dependencies:

- `faster-whisper` for local STT;
- `kokoro==0.9.4` and `soundfile` for local TTS on Python 3.11-3.12 only; Kokoro is deliberately skipped on Python 3.13+ because its package metadata excludes those runtimes, and a CUDA-capable torch/GPU is still required at runtime;
- `ddgs` for DDG library support, while provider code can fall back to HTML scraping;
- `rapidocr==3.9.2` and `onnxruntime>=1.20,<2` for local OCR, and `psd-tools` for PSD previews/layer inspection;
- `PyMuPDF` for PDF forms/rendering with AGPL implications for a network-served app;
- `markitdown[docx,pptx,xlsx,xls]` for Office/EPUB extraction, pinned to a release older than 30 days.

Optional dependencies should produce clear degraded behavior when absent unless intentionally promoted to core. MarkItDown and PyMuPDF already have focused degraded-path coverage; local STT missing-`faster-whisper` behavior is a remaining coverage gap. Core runtime requirements include `httpx2` where compatibility tests depend on it. The official Docker image additionally installs `libmagic1` plus `python-magic==0.4.27` for content-based upload MIME sniffing; that pairing is image-owned because `python-magic` needs the system shared library at import time.

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

`docker-compose.yml` starts Odysseus, ChromaDB, SearXNG, and ntfy. It binds services to loopback by default through `APP_BIND`, `CHROMADB_BIND`, and `NTFY_BIND`, persists configurable `APP_DATA_DIR`/`APP_LOGS_DIR`, SSH identity, HuggingFace cache, and user-local Python installs, and gives the Odysseus container host-loopback reachability through `host.docker.internal`.

Compose variants forward `ODYSSEUS_TTS_CACHE_MAX_BYTES`, defaulting in the service to 500 MiB, and run the mounted `scripts/migrate_searxng_settings.py` helper so retained SearXNG YAML gains default inheritance without replacement. The helper preserves file metadata and formatting where possible and writes atomically; migration failure is non-fatal to the wrapper command. MCP OAuth callback setup follows `OAUTH_REDIRECT_BASE_URL`, `APP_PUBLIC_URL`, or the launcher/bind `APP_PORT`, so externally remapped deployments should set a public base explicitly.

`Dockerfile` builds a Python 3.14 slim image with Node/npm, tmux, OpenSSH client, git/cmake, the pinned Docker CLI `29.6.2`, `gosu`, `libmagic1`, and the image-only `python-magic` wrapper.

`docker/entrypoint.sh` owns writable path ownership repair, PUID/PGID user/group creation and privilege drop, optional host-Docker socket group handling, vLLM/CUDA environment defaults, idempotent `setup.py`, and final uvicorn execution.

Docker does not mount the host Docker socket by default. Mounting it would grant powerful host access and is outside the default trust boundary. `docker/host-docker.yml` is the explicit opt-in overlay and sets `ODYSSEUS_ENABLE_HOST_DOCKER=true`; tests guard that the default and GPU compose files do not enable host Docker accidentally.

Compose selects `ODYSSEUS_IMAGE` (default GHCR `latest`) alongside a local build and publishes container port 7000 on default host port 7011. SearXNG is tag-and-digest pinned. `docker/host-workspace.yml` explicitly mounts a selected host workspace; `docker/host-network.yml` opts into Linux host networking, resets published ports, and starts uvicorn on the configured host bind/port. These overlays expand access beyond default deployment. The image includes Chromium, Noto CJK fonts, network inspection utilities, pinned `agent-browser@0.35.0`, and redistribution notices. Entrypoint ownership repair touches HuggingFace cache roots without traversing existing model files.

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

- `launch-windows.ps1` requires Python 3.11+, creates `venv`, installs `requirements.txt`, runs `setup.py`, discovers per-user Git Bash installs where possible, warns when Git Bash is missing, and starts uvicorn on port 7000 by default.
- `launcher.py`, `Odysseus.spec`, and `build-windows-portable.ps1` own the PyInstaller-style portable Windows launcher path, including app-root/data-dir differences covered by `src.runtime_paths`.
- `start-macos.sh` reads `.env`, defaults to port 7860 to avoid AirPlay conflicts, prefers Homebrew arm64 Python, installs/tolerates Homebrew Cookbook deps, handles Chroma package conflicts, starts ChromaDB for native runs, runs `setup.py`, and starts uvicorn.
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

`LOG_LEVEL` is the shared process logging toggle. CLI helpers default it to
`WARNING` to keep JSON command output clean; the web app defaults it to `INFO`
and applies it to root, console, rotating-file, and direct-uvicorn logging.
Shell completions in `scripts/_completion/` introspect CLI `--help` output through the venv and cache subcommands.

`scripts/odysseus-*` provide local CLI surfaces for backup, calendar, contacts, Cookbook, docs, gallery, logs, mail, MCP, memory, notes, personal docs, presets, research, sessions, signatures, skills, tasks, theme, and webhooks.

When route/API behavior changes, check whether a matching CLI script depends on the old shape. There is no central CLI scrubber: each credential/log/mail/task/backup/MCP/webhook script owns its own sensitive-output behavior.

## GitHub Metadata

`.github/` owns issue/PR templates, a copyable PR review template, description-check workflows, security/governance workflows, Docker publishing, and CI. Current CI runs on pushes to `main` and `dev` plus pull requests, compiles Python, checks committed frontend outputs, validates frontend architecture/syntax, emits report-only focused-test guidance, and partitions pytest across four file-based shards through `tests/_shards.py`. Pytest setup requires functional bubblewrap PID/mount containment, FFmpeg and Chromium. Its prose-only bypass excludes `website/` and `assets/branding/`; those published-file contracts remain tested.

`CONTRIBUTING.md` owns the branch model: PRs target `dev`; `main` is the curated user-running branch fast-forwarded from stable `dev` commits. Contributors who accidentally target `main` should retarget the PR base without rebasing.

PR description checks:

- run on `pull_request_target`;
- check out only base-branch `.github/scripts`;
- skip bot PRs;
- require Summary, Linked Issue, Type of Change, duplicate-search checklist, and substantive How to Test content as the hard description gate;
- classify changed paths as docs-only, tooling, backend/runtime, or UI-sensitive from GitHub's file API while executing only base-branch checker code;
- treat app-run and screenshot/clip checkboxes as author attestations, require an actual media link/attachment for UI-sensitive changes, and report runtime/visual evidence gaps separately from malformed descriptions;
- serialize mergeability labeling behind description validation and avoid granting `ready for review` to drafts or changes with outstanding runtime/visual evidence;
- update a bot comment and reconcile `ready for review`, `needs work`, `needs runtime validation`, and `needs visual evidence` labels where those labels exist.

Issue description checks:

- validate bug or feature sections based on labels;
- require bug reports to include the exact 12-character revision/date shape produced by `git show -s --abbrev=12 --format='%h (%cs)' HEAD`;
- flag unfilled dropdown placeholders such as `-- Please Select --`;
- route public vulnerability reports toward GitHub Security Advisories;
- update a bot comment and swap status labels;
- remove the workflow-owned review label when an issue closes so closed issues do not retain stale readiness state.

Security metadata includes container Trivy SARIF upload, Dockerfile lint, dependency review, secret scan, workflow security linting, GitHub default-setup CodeQL, Dependabot metadata, and hardened PR/issue description checks that avoid unsafe head-branch execution. `website/security-ci.md` documents CodeQL as a dynamic GitHub default-setup workflow; the repo should not add a checked-in CodeQL workflow while that default setup is active.

`scripts/pr_blocker_audit.py` is a read-only maintainer/contributor triage helper documented in `website/pr-blocker-audit.md`. It can fetch or ingest open PR metadata, estimate hot files and possible duplicate groups, and emit Markdown, JSON, or terminal reports. Its duplicate/blocker output is advisory, not an authority that a PR is blocked.

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
./venv/bin/python -m py_compile app.py routes/*.py src/*.py
node --check static/js/changed-file.js
docker compose config
docker compose up -d --build
docker compose logs --tail=120 odysseus
```

Run the app for user-facing or integration changes. Unit tests and syntax checks do not replace end-to-end verification for UI, Docker, provider, auth, or routing behavior.

## Shared Test Helpers

`tests/helpers/` owns reusable test scaffolding. `cli_loader.load_script()` loads CLI files without running their `main()` entrypoint. `db_stubs` owns small DB stand-ins for tests that should not import a real app database. `import_state` owns conservative `sys.modules` and parent-module-attribute restoration for tests that install fake modules or import route files under alternate stubs. `tests/README.md` documents helper conventions and review expectations.

`tests/test_runtime_behavior_regressions.py` drives the agent loop with a fake streaming model to check offered tools, clarification/plan policy, explicit native request scope and mocked browser cleanup. Negative-web cases require a model call, an answer delta and terminal completion while withholding web tools. These deterministic runtime assertions do not establish live-model or browser acceptance.

## Harness And Publication Boundaries

`tests/conftest.py` defaults the static origin to an ephemeral loopback port and publishes/restores `ODYSSEUS_TEST_STATIC_ORIGIN`; an explicit validated port can still collide. Threaded serving avoids idle-connection head-of-line blocking. Per-process random private data/mail/embedding/cache/temp paths and optional xdist basetemp avoid sharing runtime state; explicit basetemp wins. Disposable database helpers use isolated factories/cleanup rather than ORM reloads. A teardown guard removes and fails newly leaked bare `src`/`core` modules, using exact ModuleType/falsey-file detection; preexisting modules and mocks are outside that guard. The Unix socket path length fixture is character-bounded, not a universal byte-length proof.

`tests/_shards.py` partitions collected files deterministically by weights and lexical tie-breaks across four CI shards; deselection occurs after collection/import. Optional parallel smoke and serial-only guards do not make every test xdist-safe. Pytest CI uses Node 20, locked npm dependencies, cached Chromium downloads keyed by OS/lockfile, system browser libraries, FFmpeg and a functional unprivileged bubblewrap probe. It uses setup-uv cache without automatic prune and pytest skip reasons/duration reporting. These workflow provisions are not evidence that every browser, media or containment test passed on another host.

`tests/helpers/document_source.py` provides ordered implementation membership and lexical construct extraction; email JS helper enumeration is immediate rather than recursive. Stylesheet helpers follow shell order and cache identities; keyframe/hygiene/import/procfs guards are bounded textual or AST checks, not full semantic control-flow/browser proofs. The computed-style fixture, browser artifacts and reported historical suite counts remain specific to their recorded environments.

`scripts/odysseus-dev` owns separate development data/database/browser-cache paths, path-hashed port blocks, service/reserved-port refusal, readiness login and fingerprinted stop state. Hash collisions, released fallback-port races and environment-derived defaults prevent a universal isolation guarantee. `scripts/odysseus-smoke` exercises deterministic model/email HTTP scenarios and reports declared gaps; it is not full browser/product acceptance and cleanup/reused-instance state remains scoped. `scripts/ref_parity_audit.py` compares heuristic added-line probes and exact filtered tree-path sets, not behavioral equivalence; mutable refs can be reread during a run. `scripts/generate_env_reference.py` checks generated reference bytes and handwritten notes from bounded AST/helper/regex discovery, not exhaustive runtime environment use; malformed Python can yield no detected rows and source citations are file-based.

`.github/workflows/deploy-pages.yml` builds `website/` into `_site`; preview encoding targets website assets and branding lives under `assets/branding/`. Docker excludes both website and branding source roots. The macOS app script uses moved branding with best-effort `sips` icon generation; no native build or license clearance follows from source inspection. Docker publishing emits version/short-SHA trace tags and inspects those plus latest, without registry-enforced immutability. Trivy jobs use the Docker Buildx driver and free local build cache before vulnerability database work; this is CI lifecycle behavior, not an instruction to prune controller state.

Description checks retain the public numeric Linked Issue requirement; an exact maintainer-preview repository allows a beginning `N/A` value while still requiring the section after comment stripping. This checks syntax, not issue existence or branch rulesets. Dependency-review blocking applies to canonical repository pull requests; fork pip-audit advice is a separate policy. None of these workflows grants publication authority.

Maintained frontend publication requires repository-owned LF text attributes on both mapped paths, checked through isolated Git attribute evaluation. It validates the complete plan before writes, copies bytes without rewriting imports or bundling and never deletes unowned static assets; publication is not a transactional live deployment. Acorn checks manifest-selected dependency direction, canonical URL identity, used legacy exceptions, exact precache arrays and cycles, not all legacy browser behavior.

## Chroma Mount And Focused Safety Regressions

All three Compose variants mount `chromadb-data:/data`, matching the current image configuration rather than pre-1.0 `/chroma/chroma`. Existing writable-layer data needs rescue before container recreation; `docs/chromadb-volume-migration.md` describes writer shutdown/cold backup, preserved backup image, SQLite/HNSW checks, empty-volume seeding, isolated staging and count comparison. `website/backup-restore.md` links that procedure. Static YAML/document tests cover declared paths and safety markers; they do not execute or certify migration against a live Chroma image.

Focused regressions now cover invalid history truncation preserving messages, failed bulk Trash moves/selective expunge, disabled-auth app configuration gates, durable cleanup message counts/delete-boundary races and generated-image ownership/cache/attribution. Test presence and historical counts do not establish a new successful run; runtime validation evidence is separate.

## Notes Extraction Validation Surface

Notes tests include domain/characterization/application/infrastructure/reminder/workflow contracts, recurrence differential cases across timezones, raw-state cloneability, draft timing and selected-context acceptance. Browser fixtures exercise canonical/facade identity, desktop/mobile/tablet interactions, teardown and first-time offline graph loading; source guards now read feature modules through `tests/helpers/notes_source.py`. `scripts/verify_frontend_runtime.py` checks all manifest outputs in a Python-only distribution, while `scripts/verify_notes_browser_runtime.py` probes an authenticated temporary native app. These require their declared dependencies/browser environment; committed receipts and screenshots are historical evidence, not a fresh acceptance run. Runtime serving still requires no frontend build or Node installation.

## Skills Extraction And Upload Contract Tests

`tests/test_skills_audit_characterization.py` freezes 22 route declarations, 28 Python interfaces and canonical OpenAPI (17 paths, 22 operations, five schemas) from committed fixtures; constrained fields, supported default factories, model configuration and aliases are checked structurally. Unsupported factories fail without execution. `tests/test_skills_audit_services.py` exercises real route/service wiring with offline model/agent/manager substitutes, including prompt seams, owner/request authority, one-use approvals and cancellation/stream closure. Fixtures do not prove arbitrary validators, live execution or authorization by themselves. ReDoS guards now scan/import `services/skills/evaluation.py`. The Gitleaks exception is constrained to one exact historical digest, the generic-api-key rule and two named paths; this maintenance does not claim a new full-history scan.

`tests/test_upload_no_login.py` covers disabled-auth access after setup, missing/unconfigured manager failures, token ownership/scopes, legacy ownerless uploads and OCR preservation on rejection. Its loopback-host parametrization does not itself change TestClient transport identity, so source behavior and narrower assertions remain the evidence for absence of a loopback bypass.

## Current Gaps

- Fresh install smoke coverage across Linux native, Docker, macOS native/app, Windows native, WSL/Git Bash, missing Node/npm, missing Chroma service, and GPU overlays remains a roadmap item.
- Frontend source/output and architecture gates exist; they do not establish full browser or platform correctness.
- Real deployment, GPU and cross-platform install verification still require separate runs beyond CI gates.
- Optional dependency behavior is broad; remaining gaps include local STT missing-`faster-whisper`, Kokoro's Python/GPU degraded matrix, and provider/OAuth combinations not covered by focused tests.
- GitHub description-check scripts and `scripts/pr_blocker_audit.py` need continued local fixtures for section parsing, placeholder stripping, label swaps, workflow-safe behavior, and duplicate/hot-file heuristics.
- Spec bootstrap rules lack meta tests for reading `_readme.md`, spec shape, `.env*` handling, draft/report placement, and shared helper conventions.
- NVIDIA helper install/`.env` mutation paths and real Docker/GPU startup are not covered by local tests.
- Bash/Zsh completion behavior is not covered.
- `tests/KNOWN_FAILURES.md` records observed environment-dependent full-suite failures and prerequisites; its measured basis is historical, so verify applicability rather than treating it as a passing waiver.
- There is no central CLI redaction/sensitive-output regression matrix across backup, logs, mail, MCP, tasks, and webhook scripts.
- Dependency/image pinning policy is mixed: Python requirements are mostly unpinned, SearXNG is pinned, Chroma image currently uses `latest`, npm uses a lockfile, and browser MCP uses cache-gated `@playwright/mcp@latest`.
