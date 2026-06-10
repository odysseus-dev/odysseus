# Cookbook And Hardware Fit

Last updated: dev@a3cb15d | 2026-06-06

## Scope

This spec covers model setup/serving and hardware fit in:

- app route registration in `app.py`;
- `routes/cookbook_routes.py`;
- `src/cookbook_serve_lifecycle.py`;
- Cookbook package/rebuild/shell integration in `routes/shell_routes.py`;
- `routes/cookbook_helpers.py`;
- `routes/hwfit_routes.py`;
- `services/hwfit/*` and `services/hwfit/data/hf_models.json`;
- durable Cookbook state in `data/cookbook_state.json`;
- helper/CLI scripts `scripts/odysseus-cookbook`, `scripts/add_hwfit_models.py`, `scripts/hf_download.py`, and `scripts/diffusion_server.py`;
- Docker GPU overlays `docker-compose.gpu-*.yml`, `docker/gpu.*.yml`, `scripts/check-docker-gpu.sh`, and `scripts/check-docker-amd-gpu.sh`;
- frontend modules `static/js/cookbook*.js`, including Cookbook running, serve, download, diagnosis, progress, and HW Fit modules;
- tests covering Cookbook helpers, routes, CLI state, package detection, frontend progress, HW Fit services, serve profiles, Docker GPU overlays, and GPU diagnostic scripts.

## Current Call Sites Include

- Cookbook modal and state modules in `static/js/cookbook*.js`;
- package readiness/install and rebuild flows through `routes/shell_routes.py`;
- direct shell exec/stream integration used by Cookbook task controls;
- model endpoint setup and serve flows;
- hardware-fit recommendations for model choices;
- image-model recommendations for diffusion serving;
- Docker GPU helper scripts and compose overlays;
- the `odysseus-cookbook` CLI using the same Cookbook state file.

## Cookbook Runtime

`routes.cookbook_routes` owns model download, setup, SSH key, cached model scan, serve, GPU state, kill-pid, state sync, Hugging Face latest lookup, serve diagnosis, and task-status endpoints. `src.cookbook_serve_lifecycle` bridges scheduled `cookbook_serve` tasks into serve/stop behavior; task/calendar scheduling ownership stays in `calendar-tasks-notes.md`.

Access policy is split by surface:

- download/setup/SSH key/cache scan/serve/GPU/kill/state/task-status are admin/internal-tool surfaces;
- `/api/cookbook/hf-latest` is authenticated-user gated;
- HW Fit routes are authenticated read/probe routes through normal middleware, not admin-only operations;
- bearer API tokens do not satisfy Cookbook admin gates.

Runtime behavior:

- POSIX and most remote flows run detached through tmux;
- local Windows uses detached process/log/pid behavior under `%TEMP%\\odysseus-tmux`;
- remote Windows uses PowerShell runner scripts;
- missing `tmux`, `docker`, or serve-engine binaries return shaped errors where possible;
- model serve auto-registers LLM or image `ModelEndpoint` rows immediately, then frontend readiness probing can repair/create fallback endpoints;
- diffusion-server serves are registered as image endpoints;
- task status handles tmux, remote Windows logs, local Windows PID/log files, HF cache completion checks, pip dependency-install success sentinels, exit-code wrappers, serve diagnosis snapshots, and scheduled serve lifecycle hooks.

`routes.cookbook_helpers` owns validation and command construction:

- repository and model IDs;
- local directories, SSH hosts/ports, GPU selectors, and tokens;
- shell quoting for Bash and PowerShell;
- pip/install fallback chains;
- safe environment prefixes;
- serve command validation;
- user-shell PATH bootstrap, Git-Bash drive-path conversion, preflight, and exit-code helpers.

Cookbook routes request shell/SSH behavior; they do not relax shell security.

## Shell Dependencies

`routes.shell_routes.py` owns Cookbook-adjacent package readiness/install, shell execution/streaming, and llama.cpp rebuild endpoints. The Cookbook UI calls these routes for dependency diagnosis, install/update actions, engine rebuilds, and tmux/reconnect/stop/kill flows. Windows uses detached log/PID wrappers where POSIX tmux is unavailable.

These are admin-only code-execution surfaces and should be reviewed with Cookbook changes even though they are implemented outside `routes.cookbook_routes.py`.

## State, Secrets, And Provenance

Cookbook state lives in `data/cookbook_state.json`.

State behavior:

- browser-facing state masks secrets;
- server-side `env.hfToken` is encrypted before storage;
- task payloads strip raw HF tokens;
- browser local storage strips HF token values;
- state POST has anti-wipe guards for server lists;
- recent server-side tasks are preserved against stale browser overwrites;
- task-status validates saved shell-bound fields before SSH/tmux commands.

Cookbook auto-registered endpoints are currently shared/null-owner rows with no API key when created by backend serve registration. Browser fallback registration goes through the normal model-endpoint route. The desired ownership policy for Cookbook-created endpoints should remain explicit.

HW Fit is an MIT-licensed llmfit adaptation; attribution lives in project acknowledgments/licenses.

## Hardware Fit

`services/hwfit/hardware.py` owns hardware detection across NVIDIA, AMD, Apple Silicon, Windows, CPU, RAM, available RAM, remote SSH, and cached host detections.

`services/hwfit/models.py`, `fit.py`, `profiles.py`, and `image_models.py` own model catalog loading, normalization, memory estimates, quantization labels, fit scoring, serve profile computation, image model ranking, and backend/format servability filtering.

`routes/hwfit_routes.py` owns the HTTP surface and manual hardware override application.

Runtime behavior:

- hardware detection uses a cache with `fresh=true` bypass;
- manual hardware replacement is a what-if simulator, not additive hardware;
- ignore switches can drop detected GPU/RAM before ranking;
- homogeneous GPU grouping targets realistic multi-GPU pools;
- image model ranking normalizes to a single-GPU fit view;
- Metal/RDNA/backend restrictions can filter otherwise fit models.
- Windows and Apple/consumer-AMD paths filter toward GGUF/llama.cpp-compatible
  choices. On multi-GPU systems, fixed GGUF target quantization that cannot be
  served by the selected backend returns `no_fit` rather than `None`.

## Platform And Degraded Behavior

- Linux, Windows/PowerShell, macOS, Docker, NVIDIA, AMD, Apple Silicon, and CPU-only systems have different command paths.
- Remote hosts are accessed through SSH helpers; Cookbook host/port/path inputs must be validated before command construction.
- HW Fit remote host/port query values currently do not share all Cookbook route-level validation before SSH probing.
- Missing local tools or failed installs should surface command/output/error detail where possible.
- GPU overlays remain optional and do not break CPU-only deployments.
- Docker GPU overlays pass host devices/env; they do not install CUDA/ROCm engines by themselves.
- NVIDIA Docker diagnostics are read-only by default, and `.env` edits/install actions require explicit flags.
- AMD Docker diagnostics are read-only and do not mutate `.env`.
- vLLM is rejected on unsupported Windows/macOS paths.
- llama.cpp CPU-only and GPU fallback scripts should preserve usable CPU paths.
- SSH probe failures, GPU driver errors, and no-GPU states should be distinguishable.
- Ollama serve can auto-pick an available port, and task stop paths should verify
  the process/session is actually gone before treating it as stopped.

## Model Catalog And Latest Lookup

HW Fit model scoring depends on `services/hwfit/data/hf_models.json`, catalog normalization, and assumptions about model formats and quantization. `scripts/add_hwfit_models.py` updates that catalog.

Hugging Face latest lookup uses external Hub metadata and can degrade to empty, unknown-size, or malformed-result behavior. Catalog drift and dynamic latest-model metadata are separate sources of recommendation drift.

## Security Policy

Admin gates must stay in place for install, serve, kill, setup, state mutation, and shell-like actions. `/api/shell/exec` is an admin primitive used by Cookbook task control and must stay in this review boundary.

Kill-pid guardrails:

- admin-only;
- PID floor;
- signal allowlist;
- validated remote host/port;
- frontend confirmation for TERM/KILL cleanup.

Shell-bound Cookbook inputs must pass helper validation before command construction. HF tokens, Cookbook state secrets, and endpoint API keys must remain encrypted or masked and must not be written back to clients in raw form.

## Testing Coverage

Existing coverage is strongest for helper validation/quoting, pip fallback and dependency-completion regressions, cached scan scripts, serve profile computation, hardware detection/ranking across AMD/NVIDIA/macOS/manual modes, Docker GPU compose overlays, Cookbook CLI state, package detection, Windows path/task helpers, and selected frontend progress regressions.

Route-level auth/security and degraded-return coverage is thinner for Cookbook admin routes, shell dependency routes, `/api/cookbook/hf-latest`, state/status edge cases, HW Fit routes, frontend JS behavior, and helper scripts such as `hf_download.py`, `add_hwfit_models.py`, and `diffusion_server.py`.

## Current Gaps

- HW Fit remote SSH host/port validation needs to be aligned with Cookbook route validation or explicitly accepted.
- Cookbook-created model endpoint ownership/shared/null-owner policy needs a deliberate decision.
- `/api/shell/exec` and Cookbook package/rebuild routes need to remain cross-referenced with shell/admin specs because they are Cookbook-critical code-execution surfaces.
- Cookbook route auth/security and degraded-return behavior need route-level tests.
- `/api/cookbook/hf-latest` needs tests locking its user-authenticated access policy and failure behavior.
- HW Fit routes need route-level tests around missing catalogs, manual overrides, `fit_only`, profiles, and image-model cases.
- Dependency install/serve diagnosis remains split across Cookbook routes, shell routes, frontend diagnosis, optional binaries, and platform-specific scripts.
- Model catalog, quantization, backend, and Hugging Face metadata drift need ongoing maintenance.
