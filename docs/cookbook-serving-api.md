# Cookbook serving API — version 1

Status: local development candidate; not deployed. Typed plan/start/stop/restart and
ownership-aware observation are implemented. Real inference is not validated.

## Contract for native chat

`GET /api/cookbook/serving/status` is admin-gated through the existing Cookbook
router. It returns `schema_version: 1` and `services: []`.

Each service has:

| Field | Meaning |
|---|---|
| `id` | Cookbook session id, or `external-ollama` for the default discovery attempt |
| `engine` | `llamacpp`, `ollama`, or separately observed `llama_cpp_python` |
| `source` | `cookbook`, `receipt` (live launch recovered without a task record), or `discovery` |
| `base_url` | Loopback HTTP origin; null for unprobed Python bindings; append `/v1` for compatible inference APIs |
| `task_status` | Persisted task status, not a health assertion |
| `exit` | Actual direct-child exit evidence `{exit_code, observed_at}` or null; never grants process control |
| `pid` | Recorded direct-runtime or legacy wrapper PID, if present |
| `process_ownership` | `owned`, `gone`, `foreign`, `unverifiable`, or `external` |
| `readiness` | `ready`, `loading`, `sleeping`, `unavailable`, `unknown`, or `error` |
| `lifecycle_state` | Ownership-aware state, including `crashed`, `ownership-lost`, `unresponsive`, `stopping`, `stopped`, `unverifiable`, and `external` |
| `models` | Runtime-reported available model identifiers |
| `loaded_models` | Ollama's already-loaded models, with context/memory fields when supplied |
| `context_length` | llama.cpp's observed per-slot `n_ctx`; null when unknown |
| `capabilities` | Runtime-reported `modalities` / `chat_template` metadata, otherwise null; never inferred from a model's name |
| `endpoint_ownership_verified` | Status can verify an exact direct-controller Windows IPv4 listener with matching live PID/creation identity; false for unavailable/ambiguous/unsupported observations. Health alone never establishes it |
| `recovery` | `observed` when the existing receipt still verifies, otherwise `not-owned`; never means restarted |
| `management` | `odysseus-managed`, `legacy-recorded`, `stale`, `unmanaged-python`, or `external` |
| `controls` | Stop requires a live verified direct-controller receipt. Explicit restart also permits a valid receipt whose process is gone, but never a live foreign/unknown PID. Recipe/file/memory preflight can still refuse restart. Start requires a separate typed plan, never a URL-based adoption |

A healthy external Ollama daemon is not an Odysseus-owned process. Its installed
models are not necessarily loaded. An external discovery row with `unavailable`
means only that the default loopback endpoint did not answer; it does not prove
Ollama is not installed. Consumers must keep readiness and ownership separate.

A wrapper whose recorded identity is gone is `crashed` (or `stopped` after a
recorded cancellation) even if another process now answers on its old port.
Missing identity never becomes ownership. Readable Windows creation tokens can
outlive process exit, so both liveness and token equality are checked.

This observation API reads the existing Cookbook state/receipts on each call.
Recreating the app can rediscover a surviving recorded process without starting
a duplicate. Nothing is automatically restarted. It does not write task state,
repair orphaned child processes or install a background supervisor. A visible
native-chat view may poll it, but it should not poll during unrelated chat work.

## Probe boundaries

- Only native local serve command hints are considered; remote/container/WSL
  command lines are not executed or interpreted as local servers.
- Only numeric loopback HTTP origins are probed, never arbitrary URLs or
  redirects. System HTTP proxy settings are ignored.
- Requests are bounded to two seconds, with four service probes at a time.
- llama.cpp: `GET /health`, `GET /models`, `GET /props` only. These metadata
  endpoints are explicitly exempt from waking llama.cpp's sleeping model.
- Ollama: `GET /api/version`, `GET /api/tags`, `GET /api/ps` only.
- No completion/generation, `/api/show`, pull, unload, model start, GPU detection,
  process signal or automatic recovery is performed.

Reference contracts checked against the official llama.cpp server README
(`tools/server/README.md`, health, props, models, sleeping state) and Ollama API
reference (version, tags, ps). Newer optional fields stay null on older runtimes.

## Typed plan/start/stop/restart

- `POST /api/cookbook/serving/plan`: validate and return a launch plan; no process is created.
- `POST /api/cookbook/serving/start`: recompute the plan server-side, then spawn the selected executable directly with an argv list, never a shell. Returns `state: starting`, not an unverified ready claim. Poll status for readiness.
- `POST /api/cookbook/serving/stop`: accepts only `session_id`. It has no PID or URL parameter. A matched server-owned `.serving.json` receipt plus PID/token sidecars and current identity verification are required. A stale token, missing provenance, external listener or browser task flag cannot authorize a signal. Returns `process_stopped`, not an assertion that any external endpoint disappeared.
- `POST /api/cookbook/serving/restart`: accepts `session_id` and optional `allow_unestimated_context` (false by default). Rebuilds a typed plan from the private receipt using current file/host-memory validation, never replays stored argv or environment. Missing files or unacknowledged unknown memory fit are refused BEFORE stopping the current process. Unknown/external ownership is never adopted. Restart requires confirmed old-process teardown before a replacement; successful spawn returns a new `session_id`, `previous_session_id` and `state: starting`, not readiness. A valid crashed/stopped receipt may recover or reuse an already-managed identical plan. This is explicit recovery, not an automatic restart policy.

Restart is two-phase, not atomic. If stop is incomplete, HTTP 409 contains the stopping result and no replacement is spawned. If replacement launch fails after confirmed stop, HTTP 409 detail includes `state: restart-failed`, `process_stopped: true` and the error; the previous runtime is NOT claimed to be still running. Clients must retain this distinction. Status polling checks replacement readiness separately. Older receipts without saved KV/context planning hints require an explicit unknown-fit acknowledgement where applicable. No blind stop-then-replay endpoint is provided.

### Bounded readiness wait (additive version-1 endpoint)

`POST /api/cookbook/serving/wait-ready` accepts `session_id` and
`timeout_seconds` (default 30, positive, maximum 300). It retains the same
human-admin/cross-site gate and rejects generic internal-tool authority. Missing
controller provenance/external sessions return HTTP 409; invalid timeout is 400.

For a valid receipt, HTTP 200 describes the observation: `ok: true` with
`state: ready` or `sleeping`, or `ok: false` with `startup-timeout`, `crashed`,
`stopping`, `stopped`, `ownership-lost` or `unverifiable`. Consumers must inspect
the payload, not equate HTTP 200 with a ready model. Ready/sleeping responses
include runtime-reported model/context fields and the base URL. Listener-to-PID
attribution is still unverified, explicitly false; health grants no stop right.

The monotonic deadline also bounds in-flight HTTP metadata probes. Process
identity and cancellation are checked before/after probes AND after timeout,
so an exit racing a stalled probe is not silently reported as a live slow
server. Timeout ends only this observation: no process is killed/restarted, no
model is loaded, and no permanent failed task state is written. A server that
becomes ready later can still be observed normally; this is not an inferred
historical startup-failure state or an automatic recovery policy.

Incremental evidence: 19 focused readiness/API cases passed (18 unchanged
group cases reused plus the final crash-race case rerun separately). Actual
tiny child processes and HTTP fixtures cover success/loading timeout/crash/
cancel and gate preservation. The crash-race fixture explicitly asserts its
probe ran; its budget includes cold client/thread setup. Native Linux execution
and real model inference remain unperformed.

Launch fields: `engine` (`llamacpp` or `ollama`), `executable`, `port` (1024–65535), `model_path` (llama.cpp only), `context_length` (default 4096), `gpu_layers` (default 0), optional `model_context_limit`, optional `kv_bytes_per_token`, and `allow_unestimated_context` (default false).

The executable must exist and match the actual server OS: `.exe` on Windows, native name on Linux. No browser-platform selection, auto-install, source build, fallback to Python bindings or model pull occurs. A llama.cpp model must be an existing GGUF file with its header. Python bindings remain observation-only, explicitly distinct from standalone llama.cpp, with unknown readiness rather than a native health guess.

CPU plans reserve the greater of 2 GiB or 20% of available host RAM, then account for the model file and a caller-supplied KV-bytes/token estimate. Effective context is capped by that estimate and an optional caller-supplied model context limit. These are estimates/hints, not parsed model guarantees. Missing KV/VRAM knowledge requires explicit acknowledgement before start; GPU layers are never enabled by default. Windows memory uses GlobalMemoryStatusEx; Linux uses MemAvailable, with no NVML or GPU command. Ollama start starts only its daemon on the explicit loopback port, with one parallel request/loaded-model slot; it does not pull or load any model. The managed child disables startup pruning and cloud features without changing any external Ollama instance.

An occupied port is refused, not scanned upward or silently adopted. Repeating the same live managed plan reuses its receipt without a duplicate process. Controller reconstruction recovers live receipts, including the narrow crash window where the launch receipt survived but the task write did not. Unknown processes are never claimed by command-line matching. Old valid PID receipts without controller provenance remain observation-only for this new API.

Mutations retain the existing human-admin native-control gate and cross-site rejection. Generic internal-tool headers are rejected; no new resource-capability bypass was added. Per-process receipt files are application provenance, not an OS sandbox against the same Windows/Linux user. A native-chat consumer must honor these existing administration rules.

Stop uses the existing identity-gated process-tree teardown: Windows force-terminates the recorded tree; POSIX uses the existing group escalation. It never authorizes from HTTP health. No automatic restart is implemented. The normal Cookbook UI start path and endpoint registrations are unchanged; the new API returns its base URL for native chat and does not rewrite Sonnet's UI or providers.

## Durable direct-child exit evidence

Typed start now launches a daemon **thread** waiting on its direct child's native
process handle; it creates no additional process, health polling or restart
policy. After actual exit, it atomically records `<session_id>.exit.json` only
while the launch receipt still matches that child. It never finalizes task state,
changes cancellation, resurrects cleared tasks, or signals a process.

Status exposes the saved `exit_code` and Unix `observed_at` timestamp through
`exit`, including after controller reconstruction. Matching receipt provenance,
PID and creation identity are required. A live owned runtime has null exit data.
Wrong provenance, malformed/non-finite timestamps and boolean/string exit codes
are unknown, not successful exit 0. External, legacy and Python-binding services
cannot acquire exit evidence through browser task fields.

This is not a detached durable supervisor: if the launching app exits before the
runtime does, its observer ends and cannot later know the exit code. Missing
evidence stays null; existing liveness/identity observation still detects the
gone process. If a thread cannot start, launch returns a warning and leaves the
valid runtime running rather than killing it merely for missing diagnostics.

Focused exit/control/API execution: **45 passed**. Actual tiny child termination
and a reconstructed reader verified the native exit code, durable recovery and
unchanged task-state bytes. Invalid evidence and observer resource failure were
checked. Existing pytest-asyncio/SQLAlchemy deprecation warnings remain.

## Bounded model-declared context planning

The planner now observes GGUF v2/v3 little-endian metadata without loading a
runtime, tensors or tokenizer model. `general.architecture` selects the exact
`[architecture].context_length` field. When known, optional plan field
`declared_context_length` reports this **training-context declaration** and caps
the requested/caller-hinted context before the existing host-memory calculation.
An understated caller hint stays stricter; an overstated hint cannot enlarge
the model-declared cap. Context below the supported minimum is refused.

The reader has a hard 8 MiB metadata extent, 4096-key limit, bounded array work
and nesting. It skips unneeded vocabulary values, rejects duplicate keys, and
returns unknown for unsupported, malformed, truncated or over-budget metadata.
It is not a model-integrity/runtime-compatibility validator or an implementation
of RoPE/YARN overrides. Unavailable metadata does not fabricate a model limit.
GGUF reference: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md

Declared context is NOT KV/RAM/VRAM fit. The original declared-context increment
passed eighteen focused context/plan/memory checks. The additive estimate below
does not turn that declaration into a runtime-fit guarantee.

### Model-derived dense KV estimate (additive version-1 plan field)

When the bounded GGUF reader finds supported ordinary attention metadata,
optional `kv_cache_estimate` contains:

- `source: gguf-dense-f16-assumption` and the declared `architecture`;
- `bytes_per_token`: `2 * block_count * kv_head_count * (key_head_length + value_head_length)`;
- `bytes_at_planned_context`: that estimate times the final capped context;
- `used_for_memory_limit`: whether the CPU available-RAM margin used this hint.

The factor 2 assumes F16 K and V storage. Key/value head sizes use explicit
metadata when present, otherwise exact `embedding_length / head_count`. Missing
KV-head count follows the GGUF MHA default. Malformed/nonintegral/zero dimensions
stay unknown rather than being repaired. Only llama, qwen2, qwen3 and gemma-family
ordinary attention declarations are supported; no MLA/SSM/hybrid formula or
model-name heuristic is applied. The same hard metadata budgets remain in force.

For CPU plans, the greater of the caller's KV hint and the model-derived estimate
can only lower context within the existing RAM reserve. A smaller caller hint
cannot erase that cap. **A derived hint alone leaves `memory_validation: unknown`
and still requires explicit unknown-fit acknowledgement before start.** It is
not saved as caller-supplied `kv_bytes_per_token`; restart re-reads the model.
Runtime cache type/layout, padding, graph/scratch/driver overhead and VRAM remain
unverified. No GPU layer, new runtime flag or model load is introduced. Ollama
daemon-only/no-pull behavior and all serving API permission gates are unchanged.

Focused current metadata/planning/memory execution: **21 passed**, 14 unrelated
cases deselected (`gguf-kv-verified.xml`). Fixtures contain metadata only, no
weights; no package/runtime/model download or NVML/GPU operation was performed.
Prior readiness/exit/control-security evidence remains reused, not rerun.

## Verification and remaining work

### Passive Windows listener attribution for native-chat consumers

Status now queries the native IPv4 TCP owner table only for a valid direct
controller receipt and an owned PID. A unique loopback/wildcard listener owner
must equal that exact PID, with liveness/creation identity checked before and
after observation. No DNS, subprocess command, network request, process signal,
external adoption or permission change is involved. Non-Windows, IPv6, wrapper/
child listeners and ambiguous/foreign owners remain unverified, not guessed.

A native-chat consumer can combine matching service/model selection, runtime
readiness and this status attribution before routing to an owned local server.
The existing stop/restart provenance gates remain independent. `wait-ready`
still performs its existing bounded health/identity observation and reports
attribution false: poll status for the stronger listener observation; it does
not turn HTTP success into ownership. No Sonnet UI/provider code was modified.

One new focused real Windows TCP/HTTP child check passed after a red baseline,
covering positive exact-controller attribution and refusal of foreign PID,
changed incarnation and non-loopback origin. This is a tiny stdlib server, not
actual llama.cpp/Ollama inference, a GPU test or native-chat end-to-end adoption.

The three focused files `test_model_serving_status.py`, `test_model_serving_control.py`, and `test_model_serving_api.py` passed **61 tests** after the explicit restart increment. They use real loopback HTTP fixtures and real tiny child processes, not inference runtimes. The API path exercises plan → spawn → readiness → reconstructed controller → explicit restart → replacement readiness → stop. It also covers missing/stale receipts, lost task-write recovery, crashed-child recovery, refusal before stop on failed preflight, incomplete stop, failed replacement, healthy external Ollama refusing stop/restart, and internal-tool/cross-site denial. Windows execution was exercised; the Linux memory branch was simulated, not run on a Linux host.

An additional route regression for replacement spawn failure passed separately after that group: HTTP 409 accurately reported the old process stopped and the replacement failure. No source changes were needed for that extra assertion.

The accepted corrective browser sequence is reused, not repeated. No model, runtime or package was downloaded; no actual llama.cpp/Ollama model was loaded. Still unverified/deferred: real CPU/GPU inference with approval, native-chat UI integration, runtime-validated KV/VRAM fit and MLA/hybrid layouts, attribution outside the exact direct-controller Windows IPv4 case above, arbitrary external-instance selection, automatic recovery/restart policy, and a continuous background supervisor. Status provides bounded on-demand health observation; it does not silently add a polling service.

## GPU/CUDA detection and VRAM-safe offload

`GET /api/cookbook/serving/gpu[?verify=true]` (admin) reports the NVIDIA GPU from the
CUDA driver API (works when NVML fails), CUDA driver major, VRAM total/free, and a
CPU/CUDA verdict per installed llama.cpp runtime (`cuda_ready`, `missing_libs`).
A runtime whose `ggml-cuda.dll` lacks its `cudart/cublas` libraries, or needs a newer
CUDA than the driver supports, is `cuda_ready: false`. `verify=true` runs
`llama-server --list-devices` (no model load) on the selected runtime.

Plans with `gpu_layers > 0` take a fresh VRAM reading, refuse CPU-only or incomplete
CUDA runtimes and missing GPUs, and clamp layers so that
`max(1.25 GiB, 15% of VRAM)` stays free. The clamp is a dense-F16 estimate from GGUF
geometry; unknown geometry is passed through with a warning.

## Verified llama.cpp runtime install

`POST /api/cookbook/llama-runtime/install` (asset `url` + `tag`) now resolves the release by tag,
requires the asset's published SHA-256 (GitHub release `digest`), and for CUDA builds also
downloads the matching `cudart-llama-bin-win-cuda-<ver>-<arch>.zip` (cudart + cuBLAS) and extracts
its DLLs next to `llama-server.exe` without overwriting. CUDA builds install under
`<tag>-cuda-<ver>` so CUDA 12 and 13 coexist. Downloads resume (`.part` + `.part.json` naming
URL and digest), are size/SHA-256 checked before publication, and a mismatch deletes the partial and
installs nothing. Cancelling keeps a resumable partial. A CUDA backend whose imported libraries are
missing is refused (`Incomplete CUDA runtime`), never installed as a silent CPU fallback. Re-running
with identical verified archives reports `already_installed` without downloading. Local
`archive_path` installs now also require a `sha256`. Runtime summary entries carry
`build`, `cuda_ready`, `missing_libs`.
