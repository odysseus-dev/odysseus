# Webapp `stop/restart --drain`: engaging the gateway's external drain (DRAIN-2)

Status: local candidate on `drain/engage-caller` (base `404bfd63`). Caller-side fix only; no Hermes/gateway
changes. Companion to DRAIN-1 (Hermes bridge, commit `59ec7cd715`), which makes goal wakes yield while draining.

## The gap

`WebappComponent.stop(drain=N)` waited up to N seconds for the gateway's `session_admissions` table to show
zero `started` turns, but never told the gateway a drain was happening. The gateway kept accepting new turns
and an active goal's wake loop kept re-starting admissions, so the wait could not converge and the operator
had to fall back to `restart --force` (which loses in-flight turns with an unknown outcome).

## The supported engagement mechanism (studied read-only in Hermes release `c6b93a58d456`)

- `gateway/drain_control.py` defines the marker contract: `{HERMES_HOME}/.drain_request.json` with payload
  `{"action": "drain", "requested_at": <UTC ISO-8601>, "principal": <str>, "epoch": <str>,
  "suppress_notification": <bool>}`. **The marker IS the control channel** — there is no other control path
  into a running gateway.
- `POST /api/gateway/drain` (`hermes_cli/web_routers/actions.py`) is a thin wrapper: `action=drain` calls
  `write_drain_request(...)`, `action=cancel` calls `clear_drain_request()`. It adds auth (token seam / cookie
  gate) and attribution, nothing else. Idempotent on both sides; re-writing refreshes `requested_at`.
- The gateway's `_drain_control_watcher` (`gateway/run_shutdown.py`) polls the marker every ~1 s:
  present-and-active → `_enter_external_drain()` sets `_external_drain_active` and `gateway_state=draining`
  (new turns refused, `run_inbound.py`); absent → `_exit_external_drain()` reverts to the serving state.
  Fully reversible; shutdown takes precedence over a mid-shutdown cancel.
- Staleness is lenient and only rejects on a *definite* verdict: an `epoch` mismatch (marker survived a
  machine re-instantiation) or `requested_at` older than `DRAIN_REQUEST_MAX_AGE_SECONDS` (3600 s).
  `epoch` is `"<boot_id>:<pid1_start>"` from `/proc`; where `/proc` is unreadable (Windows) it is `""`,
  which disables the epoch check — a missing/empty epoch is honoured, never fail-closed. A malformed marker
  reads as `{}` and is still drain-active (fail-safe toward quiescing).

Odysseus provisions the gateway with `HERMES_HOME=config['home']` and already reads that home directly
(`gateway.pid`, `state.db`), so writing the marker there is the same supported path the API uses, without
needing the dashboard auth seam. No Hermes lifecycle change is required.

## Engagement (caller side, `src/hermes_webapp_component.py`)

When `stop(drain=N)` is called with `N > 0` and `force=False`, after the ownership/ancestor guards and
**before** the idle wait:

1. Read any pre-existing marker's raw text (`None` when absent) — kept for release.
2. Atomically write (`atomic_bytes`) the begin-drain payload with `principal="odysseus-webapp"`,
   `suppress_notification=False`, a UTC `requested_at`, and an `epoch` computed by `_instantiation_epoch()`,
   a byte-for-byte mirror of the gateway's `current_instantiation_epoch()` (empty on Windows). Same-host
   stop/start never changes the epoch, so the marker stays active across the wait; a marker that somehow
   survived a machine re-instantiation would be rejected as stale by the gateway, never wedge a fresh one.
3. Run the existing idle wait unchanged. The watcher picks the marker up within ~1 s, so from then on the
   gateway itself refuses new turns and (with DRAIN-1) goal wakes yield — admissions can fall to zero.
   A `drain` shorter than the watcher's ~1 s pickup may still time out; that is the same outcome as before,
   just with a real drain attempted. The 3600 s marker max-age is far above any sane drain wait.

`restart(drain=N)` delegates to `stop`, so it inherits the same behavior. `force=True` skips both the wait
and the engagement (force means "kill now", unchanged). `drain=0` takes no new code path at all — exact
existing behavior.

## Release semantics (no stuck drain on any path)

Release = remove our marker, or **restore a pre-existing one byte-for-byte** (an operator's dashboard drain
is not cancelled by our restart; it survives it).

| Outcome | Release |
|---|---|
| Success (admissions proven 0) | Released **before** `kill_tree`, so the gateway `restart()` starts next never boots into `draining`. |
| Timeout (`ActiveRuns`, no force) | Released in the error path; the still-running gateway re-accepts turns within ~1 s. |
| Engagement failure (marker unreadable/unwritable) | `ComponentError` before anything is stopped; the atomic write leaves no partial marker. |
| Release failure on success | `ComponentError` aborts **before** the kill — gateway still running, message names the marker path for manual removal. Never a stuck drain after a kill. |
| Release failure on an error path | The release `ComponentError` is raised `from` the original exception (both visible). |
| Unexpected exception during the wait | `except BaseException` releases, then re-raises. |

Implementation note: the engagement flag (`engage_drain`) is deliberately separate from the prior-marker
content (`prior_marker`): a fresh drain's prior content is legitimately `None`, and release must still run
for it. (First candidate conflated the two and skipped release for fresh drains; the focused tests caught it.)

## Coupling with DRAIN-1

DRAIN-1's goal-wake yield triggers on `_draining OR _external_drain_active`. Engaging the external drain is
therefore sufficient on the caller side: even without DRAIN-1 merged, the gateway refuses NEW turns while
the marker is active; with DRAIN-1, in-flight goal wakes also yield and the wait converges. No gateway-repo
change was needed or made.

## Verification (disposable fixtures only — no live gateway, no restarts, no production writes)

`tests/application_modes/test_webapp_component.py::DrainEngagementTests` (9 tests) runs the real
`WebappComponent.stop/restart` against the suite's stub-gateway fixture (a local HTTP server + the same
`gateway.pid` record the real gateway writes), with scripted `_running_admissions` polls modeling the
gateway/goal-wake reaction to an engaged drain, and a real marker file in the fixture's disposable home:

1. marker engaged before the first admission poll; payload matches the gateway contract (action/principal/
   epoch/UTC requested_at/suppress_notification); released on success;
2. multi-poll wait converges once turns yield to the engaged drain (marker present across the whole wait);
3. timeout raises `ActiveRuns`, releases the marker, gateway still running;
4. `drain=0` and `force=True, drain=30` never touch the marker (exact old behavior);
5. a pre-existing operator marker is restored byte-for-byte on success AND on timeout;
6. unreadable marker location → `ComponentError`, nothing stopped;
7. failed marker write (fixture disk-full) → `ComponentError`, no marker, nothing stopped;
8. wedged release path → `ComponentError` before the kill, gateway still running;
9. `restart(drain=N)` engages and leaves no marker for the new gateway.

Regression: the pre-existing `LifecycleTests` (8 tests, incl. the original drain/stop/force/ownership tests)
pass unchanged. Interpreter: `F:/Odysseus/venv/Scripts/python.exe` (3.12.10, has the `httpx` dependency of
`kill_tree`'s import chain; the system 3.14/3.15 interpreters do not). Fixture temp writes were redirected
into the task workspace (`TMPDIR`/`TEMP`/`TMP`); `F:/Odysseus` and the live Hermes release were read-only.