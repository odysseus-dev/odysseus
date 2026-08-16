# Shell And MCP

Last updated: dev@2e2bb52 | 2026-08-16

## Scope

This spec covers shell and MCP behavior in:

- shell routes in `routes/shell_routes.py`;
- the standalone shell helper in `services/shell/service.py`;
- agent shell/background execution in `src/tool_execution.py`, `src/agent_tools/subprocess_tools.py`, `src/bg_jobs.py`, and `src/bg_monitor.py`;
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

`src.tool_execution` owns agent-side `bash` execution and the `#!bg` marker. A `bash` block whose first line is `#!bg` starts a detached background job instead of holding the chat stream open. On Windows, request-scoped workspace shell execution prefers Git Bash when available so POSIX-style agent commands and path confinement use the intended shell instead of `cmd.exe` parsing.

`src.bg_jobs` owns disk-backed job state under `data/bg_jobs.json` and `data/bg_jobs/*`. It stores wrapper scripts, logs, exit-code files, timestamps, status, and capped result text.

`src.bg_monitor` owns polling and auto-continuation. When a job finishes, it injects the job result into the session, drains the agent stream, persists only the assistant continuation plus `bg_result` metadata, and marks the job followed up.

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

Stdio and SSE connection setup registers the session, exit stack, tool list,
and status as one completed unit. If initialization or tool discovery fails
before registration, the partial `AsyncExitStack` is closed so transports do
not leak into later reconnect attempts.

`src.agent_tools.admin_tools.do_manage_mcp()` is the agent/admin tool path for MCP config and is re-exported lazily through `src.tool_implementations` for compatibility. It is narrower than the HTTP routes: add is stdio-only, command values are checked against an allowlist/denylist before persistence, and enable/disable primarily flips DB config. `scripts/odysseus-mcp` is config-only; it reads and mutates database rows, redacts env values by default, and does not report live manager connection state.

## Built-In MCP Servers

`src.builtin_mcp` owns startup registration of built-in MCP servers unless `ODYSSEUS_DISABLE_MCP` is enabled.

Python stdio built-ins:

- image generation;
- memory;
- RAG;
- email.

The optional browser built-in uses `npx -y @playwright/mcp@latest --headless --caps vision`. It is cache-gated by checking npm's `_npx` cache for the requested package and falling back to `npx --no-install`; uncached/missing browser MCP is logged with install guidance and skipped rather than blocking startup or downloading packages at boot. Python built-ins are omitted from OpenAI function schemas because native/code-block paths already describe those capabilities; the browser built-in is exposed through MCP function schemas when connected.

Built-in Python servers prepend the app root to inherited `PYTHONPATH` rather
than replacing the environment, so container/dev site-packages remain visible
on initial connect and automatic reconnect. They can be reconnected once on
tool-call failure. User-configured MCP servers return the call failure instead
of automatic reconnect.

The built-in email MCP server is owner-aware when an owner is supplied by the
caller or configured through `ODYSSEUS_MCP_EMAIL_OWNER` /
`ODYSSEUS_EMAIL_OWNER`; if owner-scoped email accounts exist and no owner is
available, email MCP fails closed instead of exposing global accounts. Other
built-in servers remain process-global/admin trust-boundary tools unless their
own subsystem spec says otherwise.

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
- Configured MCP servers start concurrently with a per-server 20-second bound;
  timeout state is stored per server and partial connection resources are
  closed before returning.
- Missing Python `mcp` dependency degrades attempted MCP connections to error status.
- Missing or uncached browser NPX package is optional and log-only during built-in startup; startup should not perform an implicit package download.
- Windows does not support POSIX PTY/tmux paths; streaming falls back to pipes or detached logfile behavior.
- Docker images include selected shell dependencies and the Docker CLI, but host Docker socket access from inside the app container remains unavailable unless the operator explicitly enables `docker/host-docker.yml`/`ODYSSEUS_ENABLE_HOST_DOCKER=true` and mounts a real socket.
- OAuth supports Google `installed` or `web` key shapes, a remote paste-back exchange page, and generic Streamable HTTP OAuth token storage through encrypted `McpServer.oauth_tokens`. Valid JSON values that are not objects are treated as empty token state and replaced by an object on the next write. Google and generic MCP OAuth share `src.mcp_oauth.REDIRECT_URI`, built from `OAUTH_REDIRECT_BASE_URL`, then `APP_PUBLIC_URL`, then `http://localhost:${APP_PORT:-7000}`, plus `/api/mcp/oauth/callback`. Reverse proxies, public domains, and Docker host-port mappings should set an explicit public base because container bind state cannot infer the browser origin.
- `services.shell.service` remains a transitional/simple facade separate from route-level compatibility behavior.

## Security And Provenance

- Admin shell is intentional host command execution; do not expose shell routes or shell tools to regular users.
- `_require_admin()` gates shell routes and MCP config routes. The internal-tool loopback can be admin-equivalent only after auth middleware validates the internal token and loopback client.
- `_reject_cross_site()` currently applies to `/api/cookbook/packages`; `/api/shell/exec`, `/api/shell/stream`, package install, rebuild, and MCP write/OAuth routes do not call it directly.
- Shell helper paths use argv-based SSH, reject option-like hosts, validate SSH ports through shared helpers, restrict remote venv characters, and allowlist package installs.
- Non-admin/public tool policy blocks `bash`, `python`, file tools, `manage_mcp`, and all `mcp__*` tools.
- MCP stdio server registration is arbitrary host process execution and is admin-only.
- MCP OAuth key/token file paths supplied through routes are confined under `data/mcp_oauth`; generic Streamable HTTP OAuth token state is encrypted in the database.
- Built-in MCP servers are local/admin trust-boundary tools and are not
  automatically equivalent to owner-scoped HTTP route behavior. Email MCP is
  the current exception with explicit owner filtering; other built-ins need
  their own owner policy before being treated as scoped surfaces.
- MCP output is external-untrusted tool output and arms the high-impact continuation gate when model-visible. Current MCP text output is still not centrally capped before model re-entry.

## Testing Notes

Current targeted coverage includes Windows PTY import degradation, PTY unsupported stream events, the cross-site helper, `ShellService` stream deadline behavior, background store/monitor basics, concurrent MCP startup, per-server timeout isolation and cleanup, MCP manager cache/reconnect args, built-in `PYTHONPATH` preservation, non-object generic OAuth-token storage recovery, MCP CLI JSON/env serialization, MCP common truncation helper, action intent shell verbs, and public blocked-tool fail-closed behavior.

The shell/MCP audit ran the targeted venv subset with 78 passing tests and one warning.

## Current Gaps

- Decide whether `/api/shell/exec`, `/api/shell/stream`, package install, rebuild, and MCP config/OAuth writes should call `_reject_cross_site()` directly.
- Add route-level shell exec/stream tests for admin gate, cross-site behavior, empty command, plain exec, timeout, PTY, tmux, and Windows detached fallback.
- Add background job tests for launch isolation, output truncation, done/failed/timeout/died states, pending follow-ups, and result text.
- Add route-level MCP CRUD/OAuth/disabled-tool tests with a fake manager and temp database.
- Add hard per-server disabled MCP execution checks or document disabled tools as prompt/schema filtering only.
- Make MCP tool indexing sensitive to disabled-map changes, not only manager generation.
- Fix stale outer prompt/cache behavior when MCP disabled tools change.
- Add one central truncation layer for MCP result text and images before model re-entry; untrusted-result marking and exact-action continuation approval are now implemented.
- Decide whether `McpServer.env` and OAuth key files need masking, encryption, and chmod beyond admin-only access.
- Decide whether built-in MCP servers should become owner-aware or remain documented as admin/global compatibility surfaces.
- Decide whether optional browser MCP cache misses should surface in `/api/mcp` status instead of startup logs only.
