# Wave 5A: deterministic browser lifecycle

Base: `a46eb7f47abaf15c799275f946d7dfe27bdee516`, branch `feature/browser-lifecycle`.
Scope is browser-specific lifecycle only. Request authority, approvals,
TurnContract, generic process containment (Wave 3-S), effects/provenance
(Wave 4), generic process lifecycle (Wave 5B) and runtime decomposition
(Wave 6) are unchanged.

## Runtimes

1. `private_browser` (`src/agent_tools/web_tools.py`, `PrivateBrowserTool`) is the
   model-facing browser. It runs the `agent-browser` CLI per action. The CLI is a
   short-lived client of a detached daemon; the daemon calls `setsid` and
   launches Chrome. Identity is `--session ody-<hash(namespace, session_id)>`.
   Other entry points: `src/research_navigator.py` (`browser_read`),
   `scripts/probe_browser_budget.py`, app shutdown in `app.py`.
2. Playwright MCP (`src/builtin_mcp.py`, server `builtin_browser`) is one global
   `npx @playwright/mcp --headless --isolated --no-sandbox` stdio server owned by
   `src/mcp_manager.py`. Its tools are hidden from the model unless
   `private_browser` is disabled or `ODYSSEUS_EXPOSE_RAW_BROWSER_MCP` is set
   (`src/agent_loop.py`, `_should_hide_raw_browser_mcp`). The two runtimes share
   no code; only Chromium discovery overlaps.

## Probe evidence (agent-browser 0.27.0, this host)

- Runtime files live in `AGENT_BROWSER_SOCKET_DIR`, else
  `$XDG_RUNTIME_DIR/agent-browser`, else `$HOME/.agent-browser`, as
  `<session>.{pid,sock,stream,version,engine}`. The socket path must stay under
  about 103 bytes.
- Every Chrome process shares the daemon's POSIX session id (sid == daemon pid).
- `close` removes the daemon, Chrome, the runtime files and the
  `agent-browser-chrome-*` profile.
- SIGKILL of the daemon alone (the previous timeout path) left 13 Chrome
  processes, the profile, a Chromium temp directory and stale pid/socket files.
- `close` against a session with no daemon bootstraps one.
- A Chrome launch failure ("No usable sandbox", "Chrome exited early") leaves
  the daemon alive; `close` cannot reach a browser.
- There is no `read` command ("Unknown command: read").
- This host blocks the Chromium sandbox for agent-browser. Tests pass
  `AGENT_BROWSER_ARGS=--no-sandbox` in the test environment only; production
  launch flags are unchanged.

## Failure modes found and their resolution

| # | Failure | Resolution |
|---|---------|------------|
| F1 | Cancellation not handled; CLI, daemon and Chrome survived until idle timeout | `execute` catches `CancelledError`, kills every CLI client of the call and cleans the session tree, then re-raises |
| F2 | Timeout/exception killed only the daemon; Chrome reparented and leaked | `browser_lifecycle.force_cleanup` kills the daemon's whole POSIX session, removes runtime files and the profile, and verifies no survivor |
| F3 | Shutdown force-kill used `os.environ` and only the legacy layout | Shutdown uses each session's recorded launch environment, closes only verified live daemons, then force-cleans and verifies |
| F4 | Missing `session_id` used agent-browser's shared `default` session | A sessionless call gets an ephemeral session that is closed and verified before the call returns |
| F5 | Launch failure left the daemon alive | Launch-failure output triggers forced cleanup and a truthful error |
| F6 | Concurrent actions on one session raced one daemon | Per-session `asyncio.Lock` serializes actions |
| F7 | Observation after a failed navigation silently showed the old page | Sessions track navigation generation, page URL and failed navigation; such observations are prefixed with an explicit stale notice and flagged `stale_observation`. A batch's navigation outcome comes from its per-command rows; when it cannot be determined the page is treated as unknown |
| F8 | Recovery recursed through `execute` with a model-visible retry flag and no overall deadline | One deadline per call (action timeout + 75s); at most one retry, only for local read-only HTML open; model-supplied `_odysseus_browser_retry` is ignored |
| F9 | `research_navigator` passed `timeout`, which the tool ignored | Passes `timeout_ms` |
| F10 | No lifecycle evidence | Every result carries `browser_lifecycle` with stages, timings, ownership, state and cleanup receipt |
| F11 | Pid lookup assumed `/run/user/<uid>`; containers without `XDG_RUNTIME_DIR` were never cleaned | Runtime root follows agent-browser's own resolution from the launch environment |
| F12 | Per-call timeout swept every Chrome under the runtime `TMPDIR`, killing other sessions | Per-call cleanup is limited to the session tree; the `TMPDIR` sweep only runs at runtime shutdown |
| F13 | `read` used a command agent-browser does not have | `read URL` runs `open` and `get text body` in one batch; success requires both rows; `read` without URL extracts the current page |
| F14 | Playwright MCP calls had no time bound | `builtin_browser` calls are bounded by `ODYSSEUS_BROWSER_MCP_CALL_TIMEOUT_S` (default 90) and are not retried |

## Lifecycle model

Session states: `idle`, `ready`, `navigation_failed`, `navigation_unknown`, `reset`, `timed_out`,
`failed`, `launch_failed`, `bootstrap_failed`, `cancelled`, `closed`. Any state
reached by forced cleanup discards the page URL so nothing earlier remains
observable. Ownership is `retained` for a chat session (bounded by
`AGENT_BROWSER_IDLE_TIMEOUT_MS`, default 300000, and cleaned at shutdown) or
`ephemeral` for a sessionless call.

The `browser_lifecycle` result field:

```json
{"session": "ody-...", "ownership": "retained", "state": "ready",
 "navigation_generation": 2, "page_url": "file:///...",
 "stages": [{"stage": "open", "ms": 210, "ok": true, "cold_start": true}],
 "elapsed_ms": 230, "cleanup": {"method": "forced", "verified": true, "...": "..."},
 "recovery_attempts": 1, "stale_observation": true, "closed_page_url": "..."}
```

Optional keys appear only when relevant.

## Ownership boundary

`src/browser_lifecycle.py` holds the browser-specific process attribution. It
claims processes only through the session's own pid file and the daemon's
POSIX session; once the daemon is gone it claims only Chrome process groups
whose root carries an `agent-browser-chrome-*` profile. Without procfs it kills
nothing. `kill_browser_tree` is the single seam to replace with the shared
process-lifecycle primitives from Wave 3-S/5B.

## Files

- New: `src/browser_lifecycle.py`, `tests/test_browser_lifecycle.py`, this document.
- Changed: `src/agent_tools/web_tools.py` (`PrivateBrowserTool` and shutdown),
  `src/research_navigator.py` (timeout argument), `src/mcp_manager.py` (bounded
  `builtin_browser` call), `scripts/generate_env_reference.py` and
  `website/configuration-reference.md` (new variable),
  `tests/test_private_browser_tool.py` (shutdown and read fakes).
- Not touched: `src/agent_loop.py`, `src/tool_execution.py`,
  `src/agent_runtime/authority.py`, approvals, task and background infrastructure.

## Limitations

- A retained session's browser is not closed when its chat session is deleted;
  it is bounded by the idle timeout and shutdown cleanup.
- The in-process session registry keeps one small record per chat session that
  used the browser until shutdown.
- Chromium temp directories outside the profile (`org.chromium.Chromium.*`) are
  not attributable to one session and are not removed by forced cleanup.
- Playwright MCP remains one global browser shared by all sessions. A timed-out
  call is abandoned but the server is not restarted, because restarting the npx
  server requires its owner task in `builtin_mcp.py`.
- The stale-observation notice marks, but does not block, an observation after
  a failed navigation.
- Forced cleanup waits synchronously, at most one second, for killed processes
  to exit, so it can run from cancellation without awaiting.
- The recovery deadline covers the action and its retry. Post-action
  observations (page errors, settled snapshot, screenshot) keep their own
  20 second bounds outside it.
