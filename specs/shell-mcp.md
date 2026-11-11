# Shell And MCP

Last updated: dev@c74f475 | 2026-10-15

## Scope

This spec covers shell and MCP behavior in:

- shell routes in `routes/shell_routes.py`;
- the standalone shell helper in `services/shell/service.py`;
- agent shell/background execution in `src/tool_execution.py`, `src/agent_tools/subprocess_tools.py`, `src/bg_jobs.py`, and `src/bg_monitor.py`;
- `src/containment.py`, `src/containment_worker.py`, `src/process_ownership.py`, `src/process_lifecycle.py`, and `src/process_reaper.py`;
- `src/browser_identity.py`, `src/browser_lifecycle.py`, `src/browser_observation.py`, and `src/agent_runtime/process_resources.py`;
- app wiring and startup/shutdown in `app.py`;
- MCP configuration routes in canonical `routes/mcp/mcp_routes.py`, with `routes/mcp_routes.py` as a compatibility shim;
- MCP runtime state in `src/mcp_manager.py`;
- generic MCP OAuth helpers in `src/mcp_oauth.py`;
- built-in server registration in `src/builtin_mcp.py`;
- persisted `McpServer` config in `core/database.py`;
- MCP tool exposure in `src/agent_loop.py`, `src/tool_index.py`, `src/tool_schemas.py`, `src/tool_parsing.py`, `src/tool_implementations.py`, and `src/tool_security.py`;
- admin MCP/tool helpers in `src/agent_tools/admin_tools.py`;
- built-in servers in `mcp_servers/*.py`;
- Settings/Admin UI in `static/js/settings.js` and `static/js/admin.js`;
- CLI helper `scripts/odysseus-mcp`;
- Docker/native dependency context in `Dockerfile` and `docker-compose.yml`.

Cookbook model-serving shell flows are covered in `cookbook-hwfit.md`; this spec owns the shared shell and MCP surfaces they reuse.

## Shell Routes

`routes.shell_routes` owns `/api/shell/exec` and `/api/shell/stream`. These routes are powerful by design and are admin-only. They execute admin-provided command strings through the host shell.

Runtime behavior:

- `/api/shell/exec` runs a bounded command and returns stdout, stderr, and exit code;
- `/api/shell/stream` streams SSE output through plain pipes, POSIX PTY, POSIX tmux log tailing, or a Windows detached-log fallback depending on request flags and platform;
- empty commands return an error result without spawning a shell;
- timeouts kill the subprocess where possible;
- disconnects can stop streaming subprocesses;
- POSIX PTY support is optional and reports an unsupported event when unavailable.

`routes.shell_routes` also owns shell-adjacent Cookbook dependency endpoints:

- `/api/cookbook/packages`;
- `/api/cookbook/packages/install`;
- `/api/cookbook/rebuild-engine`.

Those endpoints probe local or SSH-remote packages, prepend user install bins for pip CLIs, validate SSH host/port through shared route validators, validate remote venv values, and restrict package installs to allowlisted dependencies.

`services.shell.service.ShellService` is a small standalone subprocess abstraction with output caps. It does not own live route behavior, PTY/tmux paths, Windows shell selection, admin checks, or Cookbook package probes.

## Agent Shell And Background Jobs

`src.tool_execution` owns policy/dispatch for agent-side `bash` execution and the `#!bg` marker; `src.agent_tools.subprocess_tools` owns native shell/Python/host-bridge handlers. A `bash` block whose first line is `#!bg` starts a detached background job instead of holding the chat stream open. Native enforcing execution is unavailable on unsupported platforms, including Windows; compatibility/admin shell selection is a separate path and does not establish native containment.

`src.bg_jobs` owns disk-backed job state under `data/bg_jobs.json` and `data/bg_jobs/*`. It stores wrapper scripts, logs, exit-code files, timestamps, status, and capped result text.

`src.bg_monitor` owns polling and auto-continuation. A completed job follows up only after exact owner/request/thread/generation linkage is checked, including revalidation after draining the continuation stream. Busy sessions retry; missing/terminal authority does not renew itself. Only after assistant persistence is follow-up marked delivered; suppression of an unfollowable result is distinct from delivery.

Runtime behavior:

- background jobs are restart-tolerant while their state files remain;
- jobs have a maximum runtime and stale cleanup window;
- output is capped with head/tail retention;
- active sessions can defer follow-up until the next monitor pass.

## Configured MCP Servers

`routes.mcp.mcp_routes` owns admin HTTP configuration for MCP servers:

- list/add/reconnect/enable/disable/delete servers;
- list tools and per-server tools;
- update per-server disabled tool lists;
- Google OAuth authorize/callback/manual exchange pages and generic Streamable HTTP OAuth redirect handling.

`core.database.McpServer` persists transport, command, args, env, URL, enabled state, OAuth config, disabled tool names, and encrypted generic OAuth token/client state. `McpServer.env` is plaintext JSON in the database.

`src.mcp_manager.McpManager` owns live connection state, stdio/SSE/Streamable HTTP transports, sessions, tool schemas, qualified names, and tool calls. HTTP route operations update both database state and live manager state where applicable. Streamable HTTP connects in a background task, can report `connecting` or `needs_auth`, and surfaces an authorization URL when the OAuth client flow redirects. Enabled configured servers connect concurrently at startup; each server has its own 20-second connection timeout and records `timeout` state without delaying siblings. The startup task has no second outer timeout.

Stdio and SSE connection setup registers the session, exit stack, tool list, and status as one completed unit. If initialization or tool discovery fails before registration, the partial `AsyncExitStack` is closed so transports do not leak into later reconnect attempts.

`src.agent_tools.admin_tools.do_manage_mcp()` is the agent/admin tool path for MCP config and is re-exported lazily through `src.tool_implementations` for compatibility. It is narrower than the HTTP routes: add is stdio-only, command values are checked against an allowlist/denylist before persistence, and enable/disable primarily flips DB config. `scripts/odysseus-mcp` is config-only; it reads and mutates database rows, redacts env values by default, and does not report live manager connection state.

## Built-In MCP Servers

`src.builtin_mcp` owns startup registration of built-in MCP servers unless `ODYSSEUS_DISABLE_MCP` is enabled.

Python stdio built-ins:

- image generation;
- memory;
- RAG;
- email.

The optional browser built-in uses `npx -y @playwright/mcp@latest --headless --caps vision`. It is cache-gated by checking npm's `_npx` cache for the requested package and falling back to `npx --no-install`; uncached/missing browser MCP is logged with install guidance and skipped rather than blocking startup or downloading packages at boot. Image, memory and RAG Python built-ins normally defer to native/code-block descriptions; email remains a qualified MCP schema/prompt exception. Connected browser schemas do not authorize page execution.

Built-in Python servers prepend the app root to inherited `PYTHONPATH` rather than replacing the environment, so container/dev site-packages remain visible on initial connect and automatic reconnect. `McpManager.call_tool()` can reconnect a built-in once after an exception only when the call has no bound backend identity. Bound calls return the failure so a fresh connection cannot silently replace the sealed backend incarnation. User-configured MCP servers return the call failure instead of automatic reconnect. Browser calls have a bounded timeout; timed-out calls are abandoned without retry because page state is unknown.

The built-in email MCP server is owner-aware when an owner is supplied by the caller or configured through `ODYSSEUS_MCP_EMAIL_OWNER` / `ODYSSEUS_EMAIL_OWNER`; if owner-scoped email accounts exist and no owner is available, email MCP fails closed instead of exposing global accounts. Other built-in servers remain process-global/admin trust-boundary tools unless their own subsystem spec says otherwise.

## Agent MCP Exposure

`McpManager` owns raw qualified tool calls named `mcp__{server_id}__{tool_name}`. It does not own admin, owner, public-user, or disabled-tool policy; callers must enforce policy before dispatch.

Current exposure path:

- `routes.mcp.mcp_routes` stores disabled tool names;
- `src.agent_loop` loads disabled maps for prompts/schemas;
- `McpManager.get_all_openai_schemas()` and prompt descriptions filter disabled tools;
- `src.tool_index` indexes MCP prompt descriptions by manager generation;
- `src.tool_security` blocks all `mcp__*` tools for non-admin/public users;
- `src.tool_execution` dispatches received `mcp__*` calls to `McpManager.call_tool()`.

Per-server disabled MCP tools currently hide tools from prompts/schemas while listings still return tools with disabled metadata. They are not a complete execution-time gate if a disabled qualified name reaches tool execution. Plan mode additionally asks `McpManager.plan_mode_blocked_mcp()` to hide write/unknown MCP tools and add qualified names to the runtime disabled set for that turn.

After model-visible external/workspace context, arbitrary MCP actions classify fail-high and require an exact one-use approval unless a specific low-impact capability classification says otherwise. MCP results are marked external-untrusted for continuation security even when a call returns a failed status with remote payload.

## Degraded And Platform Behavior

- `app.py` starts the background monitor and MCP startup tasks asynchronously; MCP startup is non-critical to app readiness.
- Configured MCP servers start concurrently with a per-server 20-second bound; timeout state is stored per server and partial connection resources are closed before returning.
- Missing Python `mcp` dependency degrades attempted MCP connections to error status.
- Missing or uncached browser NPX package is optional and log-only during built-in startup; startup should not perform an implicit package download.
- Windows does not support POSIX PTY/tmux paths; streaming falls back to pipes or detached logfile behavior.
- Docker images include selected shell dependencies and the Docker CLI, but host Docker socket access from inside the app container remains unavailable unless the operator explicitly enables `docker/host-docker.yml`/`ODYSSEUS_ENABLE_HOST_DOCKER=true` and mounts a real socket.
- OAuth supports Google `installed` or `web` key shapes, a remote paste-back exchange page, and generic Streamable HTTP OAuth token storage through encrypted `McpServer.oauth_tokens`. Valid JSON values that are not objects are treated as empty token state and replaced by an object on the next write. Google and generic MCP OAuth share `src.mcp_oauth.REDIRECT_URI`, built from `OAUTH_REDIRECT_BASE_URL`, then `APP_PUBLIC_URL`, then `http://localhost:${APP_PORT:-7000}`, plus `/api/mcp/oauth/callback`. Reverse proxies, public domains, and Docker host-port mappings should set an explicit public base because container bind state cannot infer the browser origin.
- `services.shell.service` remains a transitional/simple facade separate from route-level compatibility behavior.

## Security And Provenance

- Admin shell is intentional host command execution; do not expose shell routes or shell tools to regular users.
- `routes.shell_routes._require_admin()` gates shell and selected Cookbook native controls, rejecting internal-tool identity/header administration. Auth-disabled operator access requires direct loopback without forwarding or disallowed browser-origin metadata; an Authorization header alone is not excluded by this transport helper. Auth-enabled administration requires a present auth manager and human admin. MCP configuration instead uses common `core.middleware.require_admin()`, which admits a matching internal token/sentinel or explicit auth-disabled mode; it does not inherit the shell gate's transport/resource rules.
- `_reject_cross_site()` currently applies to `/api/cookbook/packages`; `/api/shell/exec`, `/api/shell/stream`, package install, rebuild, and MCP write/OAuth routes do not call it directly.
- Shell helper paths use argv-based SSH, reject option-like hosts, validate SSH ports through shared helpers, restrict remote venv characters, and allowlist package installs.
- Non-admin/public tool policy blocks `bash`, `python`, file tools, `manage_mcp`, and all `mcp__*` tools.
- MCP stdio server registration is arbitrary host process execution and is admin-only.
- MCP OAuth key/token file paths supplied through routes are confined under `data/mcp_oauth`; generic Streamable HTTP OAuth token state is encrypted in the database.
- Built-in MCP servers are local/admin trust-boundary tools and are not automatically equivalent to owner-scoped HTTP route behavior. Email MCP is the current exception with explicit owner filtering; other built-ins need their own owner policy before being treated as scoped surfaces.
- MCP output is external-untrusted tool output and arms the high-impact continuation gate when model-visible. Current MCP text output is still not centrally capped before model re-entry.

## Testing Notes

Current targeted coverage includes Windows PTY import degradation, PTY unsupported stream events, the cross-site helper, `ShellService` stream deadline behavior, background store/monitor basics, concurrent MCP startup, per-server timeout isolation and cleanup, MCP manager cache/reconnect args, built-in `PYTHONPATH` preservation, non-object generic OAuth-token storage recovery, MCP CLI JSON/env serialization, MCP common truncation helper, action intent shell verbs, and public blocked-tool fail-closed behavior.

The shell/MCP audit ran the targeted venv subset with 78 passing tests and one warning.

## Contained Execution And Native Browser Identity

Model-reachable native shell/Python execution uses `src.containment.agent_spec()` with enforcing filesystem, process-tree, and wall-clock requirements. Missing required mechanisms return containment-unavailable results before code runs; optional network/memory/process-count dimensions are recorded separately and must not be assumed enforced. Bubblewrap provides the Linux filesystem/namespace path when available. Direct admin HTTP shell routes remain a distinct operator surface. Host/client bridges declare external execution rather than claiming local sandbox enforcement.

Process identity is PID plus start token, not PID alone. `src.process_ownership` probes identities; `src.process_lifecycle` protects the server process group and reports death only after observation. Durable callers require positive PID/start-token identity; the generic tree helper also retains a compatibility mode without that requirement, so this is not a universal signaling guarantee. Background jobs persist launch generation, containment, and process identity so stop/restart paths can refuse stale targets.

`private_browser` currently admits only `session_info`. All page navigation, snapshots, screenshots, close/batch/recovery actions and raw global Playwright browser operations are denied unconditionally at the common boundary before binding or dispatch. `src.browser_identity` and lifecycle/observation modules retain owner/thread/session/incarnation machinery and metadata-only prompt support, but these do not enable page execution. The pinned agent-browser 0.35 Linux x64/arm64 discovery, clean environment and read-only bounded CDP observations do not amount to usable browser acceptance; metadata lookup does not adopt or create an unrelated launch.

`McpManager` additionally registers backend incarnations and exposes server-owned resource observations for admitted calls. Built-in connection owner tasks keep AnyIO transport scopes alive until disconnect; changed connections invalidate sealed backend identities. Existing transport configuration and schema discovery remain manager-owned.

## Launch, Cleanup And Bridge Boundaries

Linux native containment requires a functional bounded bubblewrap mount/PID probe, not merely an installed executable. The supervisor/worker ready acknowledgement holds user code until containment publication, durable job state, sidecar and exact launch receipt are verified. Filesystem, process-tree and wall-clock dimensions are required; missing mechanisms fail before code execution. Captured init/leader PID plus start tokens and optional pidfd support cleanup; both deaths must be observed. Unknown identity remains unverified, foreign identity is never a signal target, and no later PID lookup can manufacture a launch receipt. Startup timeout includes bootstrap/stdin; cancellation shields spawn cleanup. Optional network/memory/process-count dimensions are not implied enforcement.

Native background work uses generation-bound launches rather than agent tmux. Operator HTTP tmux remains a compatibility surface. PTY cleanup freezes known descendants before signaling and protects the server group; missing launch PID/token does not trigger PID/group recapture. Unverifiable containment grants remain active; detached jobs with foreign/unverifiable ownership can retire failed without signalling, retaining follow-up history rather than declaring successful cleanup. Publication retirement checks exact launch identity and its caller-specific lifetime rule; foreground startup recovery can also remove publications whose receipt is genuinely absent. Unreadable or malformed nonempty startup state is retained. Dead launch retention/pruning and delivered result retention are bounded lifecycle policies, not proof of task success. Store transaction locks serialize background read/modify/write, not every application store.

`core.platform_compat.service_home()` uses `Path.home()` on Windows and lazy POSIX real-UID passwd lookup. Successful POSIX lookup keeps a child HOME override from choosing the service cache; the missing-entry/OS-error fallback uses `Path.home()` and can honor that override. Default native child environments allow selected keys with fixed HOME/terminal values; an explicitly supplied subprocess environment is not value-scrubbed. This is separate from inherited built-in MCP environments.

Host shell/file bridges declare external execution and normalize the admitted URL authority while replacing the operation path and dropping query/fragment components. Every bridge run/read/write/patch/poll/cancel client uses `trust_env=False`. HTTP admission accepts the explicit loopback/host-docker/LAN/CGNAT or actual Docker-gateway cases, not every private network or arbitrary TLS host; this bridge rule does not apply to every other HTTP client. SSH/external raw shell calls cannot borrow native launch/stop authority. Cookbook and scheduled native launches carry sealed backend capabilities; missing capabilities fail closed.

MCP add-server argument text must decode to a JSON array before mutation; an empty value means an empty array, without an element-type guarantee. Bound backend calls cannot silently reconnect into a different incarnation. Browser timeout abandonment remains no-retry: EOF or partial transport state is not exactly-once execution proof. Email MCP owner and session ContextVars are reset in finally; its owner-filtered listing/index behavior is documented with email ownership.

## Compatibility Process Evidence

Python execution confinement validates the read-only interpreter-prefix mounts it relies on instead of treating an arbitrary interpreter path as admitted. Process inspection helpers retain platform-specific uncertainty: unavailable procfs inspection is not proof that a process died, and generic compatibility liveness helpers are not substitutes for the stored exact launch identities required by background execution cleanup.

## Result Publication And Continuation

Native execution reports containment, teardown and capture truncation independently. Verified timeout uses exit 124; unverified process death is teardown failure rather than successful cleanup, and Python child-runtime failure cannot hide behind exit zero. Detached supervisors publish result JSON before exit metadata; setup failure releases an unstarted grant and falls back to stderr when its failure log cannot be written. Successful exit does not imply complete captured output.

PTY teardown freezes its leader identity and verified session group. FOREIGN identity is not signalled, absent/unverifiable identity does not prove death, and a gone leader can permit cleanup of its recorded surviving group without rediscovering a replacement PID. Timeout SSE reports surviving processes; quiet disconnect/error paths log failed cleanup. Generic compatibility tree helpers still require durable callers to request identity verification.

Background historical lookup does not reap unrelated cached handles: selected running records need their exact frozen PID/start token. Continuation validates immutable job, owner, request, thread and authority linkage before and after awaits, settles the linked launch effect from lifecycle facts rather than output, and persists delivery before marking followed-up. Missing sessions or invalid linkage become terminal_unfollowable; busy sessions/managers retry, and a changed snapshot prevents stale suppression. This is not an atomic lock over the entire chat session.

Foreground startup retirement retains unreadable/malformed stores. With an existing receipt it requires exact linkage and an observed GONE/FOREIGN manager; live or unverifiable managers retain publication even after child teardown. A genuinely missing receipt also permits retirement of a valid foreground publication because attachment cannot resume. Ordinary producer-finally retirement uses verified teardown. One-use launch reservations are shared across approval copies, so deleting publication does not renew dispatch. Retirement is bookkeeping, not authority to signal or proof of process-tree death. Protected-root overlays are refused after normalization; an external bridge response heuristic does not attest local effects.

## Image MCP Ownership

`mcp_servers/image_gen_server.py` reads the hidden trusted-dispatch `_odysseus_owner`, trims blank values to null, stamps `GalleryImage.owner`, and registers a fresh image after gallery commit when an owner exists. There is no image-owner environment fallback. `src/tool_execution.py` removes caller-reserved attribution fields before injecting server owner context for legacy and qualified image calls; direct fallbacks retain the trusted owner. Generated-file access uses the independent fail-closed policy in `gallery-editor-media.md`.

## Current Gaps

- Decide whether `/api/shell/exec`, `/api/shell/stream`, package install, rebuild, and MCP config/OAuth writes should call `_reject_cross_site()` directly.
- Shell route/service and process-lifecycle tests cover selected execution paths; PTY/tmux/Windows behavior remains platform-dependent.
- Background identity, containment, and follow-up paths have focused tests; restart/platform integration still needs separate validation.
- Add route-level MCP CRUD/OAuth/disabled-tool tests with a fake manager and temp database.
- Add hard per-server disabled MCP execution checks or document disabled tools as prompt/schema filtering only.
- MCP prompt descriptions cache against both disabled maps and manager generation; downstream index refresh still needs coordinated invalidation.
- Add one central truncation layer for MCP result text and images before model re-entry; untrusted-result marking and exact-action continuation approval are now implemented.
- Decide whether `McpServer.env` and OAuth key files need masking, encryption, and chmod beyond admin-only access.
- Decide whether built-in MCP servers should become owner-aware or remain documented as admin/global compatibility surfaces.
- Decide whether optional browser MCP cache misses should surface in `/api/mcp` status instead of startup logs only.
