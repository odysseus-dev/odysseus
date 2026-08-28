# Cookbook And Hardware Fit

Last updated: dev@e71f8ce | 2026-08-25

## Scope

This spec covers model setup/serving and hardware fit in:

- app route registration in `app.py`;
- `routes/cookbook_routes.py`;
- `src/cookbook_serve_lifecycle.py`;
- `src/host_docker_access.py`;
- Cookbook package/rebuild/shell integration in `routes/shell_routes.py`;
- `routes/cookbook_helpers.py`;
- `routes/hwfit_routes.py`;
- `services/hwfit/*` and `services/hwfit/data/hf_models.json`;
- durable Cookbook state through `routes.cookbook_helpers.COOKBOOK_STATE_FILE`;
- helper/CLI scripts `scripts/odysseus-cookbook`, `scripts/add_hwfit_models.py`, `scripts/hf_download.py`, and `scripts/diffusion_server.py`;
- Docker overlays `docker-compose.gpu-*.yml`, `docker/gpu.*.yml`, `docker/host-docker.yml`, `scripts/check-docker-gpu.sh`, and `scripts/check-docker-amd-gpu.sh`;
- frontend modules `static/js/cookbook*.js`, including Cookbook running, serve, download, diagnosis, progress, and HW Fit modules;
- tests covering Cookbook helpers, routes, CLI state, package detection, frontend progress, HW Fit services, serve profiles, Docker GPU overlays, and GPU diagnostic scripts.

## Current Call Sites Include

- Cookbook modal and state modules in `static/js/cookbook*.js`;
- package readiness/install and rebuild flows through `routes/shell_routes.py`;
- direct shell exec/stream integration used by Cookbook task controls;
- model endpoint setup and serve flows;
- hardware-fit recommendations for model choices;
- image-model recommendations for diffusion serving;
- APFEL/local platform dependency paths where supported;
- Docker GPU helper scripts and compose overlays;
- the `odysseus-cookbook` CLI using the same Cookbook state file.

## Cookbook Runtime

`routes.cookbook_routes` owns model download, setup, SSH key, cached model scan, serve, GPU state, kill-pid, state sync, Hugging Face latest lookup, vLLM recipe lookup, serve diagnosis, and task-status endpoints. `src.cookbook_serve_lifecycle` bridges scheduled `cookbook_serve` tasks into serve/stop behavior; task/calendar scheduling ownership stays in `calendar-tasks-notes.md`.

Access policy is split by surface:

- download/setup/SSH key/cache scan/serve/GPU/kill/state/task-status are admin/internal-tool surfaces;
- `/api/cookbook/hf-latest` is authenticated-user gated;
- HW Fit routes are authenticated read/probe routes through normal middleware, not admin-only operations;
- bearer API tokens do not satisfy Cookbook admin gates.

Runtime behavior:

- POSIX and most remote flows run detached through tmux;
- local Windows uses detached process/log/pid behavior under `%TEMP%\\odysseus-tmux`; Python first publishes a valid Win32 fallback PID, then Git Bash may replace it with `/proc/$$/winpid` after a ready-file handoff, so PowerShell `Stop-Tree` can terminate the actual serving shell and children instead of receiving an MSYS PID. Frontend PowerShell venv activation is quoted safely and the local Git Bash runner converts a valid `Scripts\\Activate.ps1` prefix into `source <git-bash-path>/Scripts/activate` so the selected environment actually supplies the serve binary;
- remote Windows uses PowerShell runner scripts;
- missing `tmux`, `docker`, or serve-engine binaries return shaped errors where possible;
- local Docker inside the Odysseus container is available only when the Docker CLI exists, `ODYSSEUS_ENABLE_HOST_DOCKER=true`, and `/var/run/docker.sock` is actually mounted as a socket; otherwise Cookbook should show the host-Docker access hint and prefer remote SSH Docker workflows;
- model serve auto-registers LLM or image `ModelEndpoint` rows immediately, then frontend readiness probing can repair/create fallback endpoints;
- diffusion-server serves are registered as image endpoints;
- MLX image serves use `scripts/mlx_image_server.py`, which pins generation/edit dispatch to the model chosen at process start and ignores OpenAI-compatible per-request model selectors;
- vLLM recipe routes fetch and cache model recipe manifests/YAML from `vllm-project/recipes`, normalize base args/env/dependencies/tool-calling/reasoning variants, and expose compatible strategy metadata for serve setup;
- Hugging Face download/setup paths can detect and persist encrypted HF tokens for later Cookbook/agent use;
- local and remote model paths can contain spaces or non-ASCII characters when helper validation/quoting accepts them;
- task status handles tmux, remote Windows logs, local Windows PID/log files, HF cache completion checks, stale browser-state download guards, pip dependency-install success sentinels, exit-code wrappers, serve diagnosis snapshots, and scheduled serve lifecycle hooks;
- scheduled serve lifecycle stop attempts only persist `status=stopped`, clear `_scheduledStopAtMs`, and delete auto-registered endpoints for sessions whose tmux/remote stop command succeeded or were already gone; failed stop attempts are logged without marking unrelated expired serves as stopped.

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

Cookbook state lives under the shared data dir through the `COOKBOOK_STATE_FILE` constant, normally `data/cookbook_state.json`. Routes and the `odysseus-cookbook` CLI use the same state path.

State behavior:

- browser-facing state masks secrets;
- server-side `env.hfToken` is encrypted before storage;
- task payloads strip raw HF tokens;
- browser local storage strips HF token values;
- state POST has anti-wipe guards for server lists;
- state POST rejects stale `done` download state when the latest shard/cache markers still show an incomplete download;
- recent server-side tasks are preserved against stale browser overwrites;
- task-status validates saved shell-bound fields before SSH/tmux commands.

Cookbook auto-registered endpoints are currently shared/null-owner rows with no API key when created by backend serve registration. Browser fallback registration goes through the normal model-endpoint route. The desired ownership policy for Cookbook-created endpoints should remain explicit.

HW Fit is an MIT-licensed llmfit adaptation; attribution lives in project acknowledgments/licenses.

## Hardware Fit

`services/hwfit/hardware.py` owns hardware detection across NVIDIA, AMD, Apple Silicon, Windows, CPU, RAM, available RAM, remote SSH, container/native probe context, and cached host detections.

`services/hwfit/models.py`, `fit.py`, `profiles.py`, `image_models.py`, and
`hf_discovery.py` own model catalog loading, normalization, API-backed dynamic
catalog refresh, memory estimates, quantization labels, fit scoring, serve
profile computation, image model ranking, and backend/format servability
filtering.

`routes/hwfit_routes.py` owns the HTTP surface and manual hardware override application.

Runtime behavior:

- hardware detection uses a cache with `fresh=true` bypass;
- probe results include scope/container visibility metadata, and containerized no-GPU/low-RAM states can return user-facing visibility warnings with rescan/manual/copy-diagnostics actions;
- manual hardware replacement is a what-if simulator, not additive hardware;
- manual hardware accepts `cuda`, `rocm`, `metal`, `cpu_x86`, and `cpu_arm`
  backends and must stay in lock-step with backend support in `fit.py`. Metal
  simulation marks unified memory and filters toward locally servable GGUF/MLX
  choices instead of CUDA/vLLM-only formats.
- ignore switches can drop detected GPU/RAM before ranking;
- homogeneous GPU grouping targets realistic multi-GPU pools;
- image model ranking normalizes to a single-GPU fit view;
- Metal/RDNA/backend restrictions can filter otherwise fit models.
- Apple Silicon bandwidth estimates use chip/core-specific tables for M-series Max/Pro/Ultra variants and avoid matching non-Apple GPU names.
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
- Default Docker Compose intentionally does not mount the host Docker socket. `docker/host-docker.yml` is an explicit high-trust overlay for operators who accept broad host-Docker control from inside the container.
- NVIDIA Docker diagnostics are read-only by default, and `.env` edits/install actions require explicit flags.
- AMD Docker diagnostics are read-only and do not mutate `.env`.
- vLLM is rejected on unsupported Windows/macOS paths.
- llama.cpp CPU-only and GPU fallback scripts should preserve usable CPU paths.
- SSH probe failures, GPU driver errors, and no-GPU states should be distinguishable.
- Remote SSH host/port validation is shared through route validators for Cookbook/HWFit paths.
- Windows launcher/runtime Git Bash discovery includes per-user installs under `%LocalAppData%\\Programs\\Git`, and WSL/Git Bash detection shapes PATH handling for NVIDIA/remote flows.
- macOS startup helpers start ChromaDB alongside the app path.
- Ollama serve can auto-pick an available port, and scheduled task stop paths
  verify stop success before persisting a stopped state.

## Model Catalog And Latest Lookup

HW Fit model scoring depends on bundled `services/hwfit/data/hf_models.json`,
bundled `services/hwfit/data/mlx_community_models.json`, runtime dynamic caches
under `DATA_DIR/hwfit/`, catalog normalization, and assumptions about model
formats and quantization. `scripts/add_hwfit_models.py` updates the static HF
catalog.

Hugging Face latest lookup and HW Fit dynamic refresh use external Hub metadata
and can degrade to empty, unknown-size, partial, or malformed-result behavior.
`refresh_catalog=1` refreshes API-backed collection caches for MLX community
and selected HF organization collections, with a 24-hour freshness guard and
bundled JSON fallbacks when the network/cache is unavailable. HW Fit tolerates
non-numeric `gpu_count` values from callers. Model normalization also treats
non-string `parameter_count` and quantization fields as unknown rather than
calling string methods and aborting the ranking pass. Catalog drift and dynamic
latest-model metadata are separate sources of recommendation drift.

## Security Policy

Admin gates must stay in place for install, serve, kill, setup, state mutation, and shell-like actions. `/api/shell/exec` is an admin primitive used by Cookbook task control and must stay in this review boundary. Scheduled `cookbook_serve` tasks are admin-only action tasks; task create/update/manual run/webhook/scheduler execution must all reject or pause them for non-admin owners.

Kill-pid guardrails:

- admin-only;
- PID floor;
- signal allowlist;
- validated remote host/port;
- frontend confirmation for TERM/KILL cleanup.

Shell-bound Cookbook inputs must pass helper validation before command construction. HF tokens, Cookbook state secrets, and endpoint API keys must remain encrypted or masked and must not be written back to clients in raw form. Host Docker socket access must stay opt-in and clearly distinguished from merely having a Docker CLI in the container.

## Testing Coverage

Existing coverage is strongest for helper validation/quoting, SSH host validation, pip fallback and dependency-completion regressions, cached scan scripts, serve profile computation, scheduled serve lifecycle state persistence, hardware detection/ranking across AMD/NVIDIA/macOS/manual/container modes, MLX/Metal ranking and request-model pinning, manual backend simulation, Docker GPU compose overlays, Cookbook CLI state, package detection, Windows venv/path/task helpers, non-numeric GPU counts, non-string model catalog fields, and selected frontend progress regressions.

Route-level auth/security and degraded-return coverage is thinner for Cookbook admin routes, shell dependency routes, `/api/cookbook/hf-latest`, state/status edge cases, HW Fit routes, frontend JS behavior, and helper scripts such as `hf_download.py`, `add_hwfit_models.py`, and `diffusion_server.py`.

## Current Gaps

- Cookbook-created model endpoint ownership/shared/null-owner policy needs a deliberate decision.
- `/api/shell/exec` and Cookbook package/rebuild routes need to remain cross-referenced with shell/admin specs because they are Cookbook-critical code-execution surfaces.
- Cookbook route auth/security and degraded-return behavior need route-level tests.
- `/api/cookbook/hf-latest` needs tests locking its user-authenticated access policy and failure behavior.
- HW Fit routes need route-level tests around missing catalogs, manual overrides, `fit_only`, profiles, and image-model cases.
- Dependency install/serve diagnosis remains split across Cookbook routes, shell routes, frontend diagnosis, optional binaries, and platform-specific scripts, even though longer serve-output tails are centralized through `routes/cookbook_output.py`.
- Model catalog, quantization, backend, and Hugging Face metadata drift need ongoing maintenance.
