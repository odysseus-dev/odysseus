# Cooperative Windows application restart

Status: tested development infrastructure, NOT installed or activated in production.
Production remains `164861eff5aa111f894bcc0eb83f002f81323e11`.
Branch: `feature/windows-cooperative-restart`, based directly on that production
commit, independent of `feature/cookbook-serving-lifecycle-31d9f294`.

## What changes

`launch-windows.ps1 -RunOnly` enters an early branch before Python discovery,
pip, setup, Hermes maintenance, CUDA reconfiguration or sidecar ownership. It
requires an existing interpreter and installed dependencies. Missing resources
fail; there is no fallback into the installer. It runs the controlled Uvicorn
entrypoint and exits naturally after its child. It never owns/stops Chroma or
Hermes. The existing non-RunOnly install path is unchanged, including its old
cleanup behavior: do not use that path for future managed restarts.

The controlled runner exposes a Windows named pipe, not an HTTP mutation route.
The pipe rejects remote clients. Pipe and private-directory ACLs permit only the
current user and SYSTEM; a connected peer must also have the same user SID.
A fresh key with 256 bits of entropy authenticates a per-connection nonce and exact
request with HMAC-SHA256. JSON frames are bounded; no pickle is accepted. Both
sides verify the pipe peer's kernel PID; the client verifies the server's live
creation identity, and the server checks the exact identity requested. The key
is never put in argv, returned by status, or logged. Keep the control directory
private. This is same-user authorization, not a sandbox against that OS user
or an administrator who can alter the application.

Key rotation creates a new file object atomically inside the protected directory.
It never overwrites an old file in place: a pre-existing explicit Everyone-read
ACL or an already-open handle to the old file must not expose the new key.
Only connection opening is retried across transient named-pipe instance turnover;
no authenticated action is retried after being sent. Timeouts must be finite,
positive and at most 3600 seconds.

## Normal operation, after approved first activation

From an external terminal, using the installed Odysseus interpreter:

```
F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\scripts\windows-app-control.py status --control-dir "%LOCALAPPDATA%\Odysseus\run"
F:\Odysseus\venv\Scripts\python.exe F:\Odysseus\scripts\windows-app-control.py restart --control-dir "%LOCALAPPDATA%\Odysseus\run" --timeout 60
```

These examples use cmd.exe variable syntax. In PowerShell supply an expanded
path such as `(Join-Path $env:LOCALAPPDATA 'Odysseus\run')` instead. Do not run
restart from inside a turn: its own active lease must block it.

The coordinator holds an exclusive cross-process operation lock. Before stop,
the app pauses new HTTP admission, refuses in-flight mutations, and rechecks:

- zero unexpired turns in the selected gateway SQLite database (read-only);
- terminal-only Cookbook and app background-job inventories;
- no live/invalid detached worker PID receipts;
- no unaccounted app descendants, excluding the identity-pinned shared gateway/
  sidecar branches;
- exact current app PID/creation time and TCP listener ownership;
- original gateway and Chroma listener identities and HTTP health.

Missing files, inaccessible state, malformed inventories, unknown ownership,
active work or changed shared services block the request. No stale worker
receipt is silently deleted. Stale task statuses may conservatively block until
resolved through normal app controls. This inventories app-managed work, not
arbitrary unrelated programs on the computer.

A successful request sets Uvicorn's cooperative exit flag and the existing
streaming shutdown flag. Uvicorn drains without a forced task-cancellation
deadline. The external coordinator waits for the exact app and launcher to
exit, checks shared services again, and invokes the SAME launcher in RunOnly
mode. It verifies the new app PID/creation time, actual listener ownership,
HTTP health, authenticated control response and unchanged healthy shared
services. A spawned process alone is never success.

`--timeout` bounds each shutdown/startup wait, not total wall time including
bounded handshake/health checks. Shutdown timeout reports `shutdown-timeout`,
`forced: false`, `replacement_started: false`; the app can remain draining.
Startup timeout reports the newly spawned launcher PID and does NOT kill it or
start another copy. Inspect status/launcher.log before further action. There is
no force flag, tree kill, console broadcast or automatic escalation in the
operational implementation.

A quiet maintenance window is required: do not start new turns/jobs until the
app is healthy. This does not install a global admission fence in the independent
gateway. Direct gateway connections can survive; connections PROXIED through the
restarting app necessarily reconnect. Do not promise their TCP continuity.

Defaults match this installation: app 7000, gateway 9130, Chroma 8100; Chroma
health is `/api/v2/heartbeat`. Only numeric IPv4 loopback health origins are used,
with no proxies or redirects. The selected gateway database and job directories
must match the actual instance. Missing/unhealthy Chroma blocks this conservative
configuration; the infrastructure never installs or starts a replacement.

## Actual Windows evidence

Focused tests: `tests/test_windows_app_restart.py` and
`tests/test_windows_app_restart_integration.py`. Latest per-case evidence:
`.odysseus-dev/windows-restart-evidence.json` and referenced JUnit reports.
Eleven unique checks pass, aggregated from focused incremental runs, not a whole
repository regression. The final health-validation change was followed by its
negative test and another real successful launcher restart. Earlier unchanged
checks are reused.

The final hardening also reproduced a pre-existing permissive key-file ACL and
replaced it with an owner/SYSTEM-only file, then reran a real authenticated restart.
A transient Windows pipe-instance gap was observed between consecutive requests;
opening now retries within a deadline before any authentication/action is sent.
Non-finite/invalid deadlines are rejected before control I/O. No permissions were
widened to fix either issue, and the superseded failures remain in evidence.

Real isolated Windows processes exercised:

- hidden PowerShell -> venv redirector -> Uvicorn;
- a gateway HTTP child started UNDER the app, retained across app exit;
- a separate Chroma HTTP stand-in;
- named-pipe SID/PID authentication, wrong key and wrong identity refusal;
- actual lease/download/unknown PID/unaccounted child guard refusals;
- changed app identity plus surviving shared identities and a persistent direct
  gateway HTTP/1.1 TCP connection;
- deliberately delayed ASGI shutdown causing timeout without force/replacement;
- native ACL readback confirming a protected owner/SYSTEM-only private folder;
- a real legacy-chain first-transition rehearsal described below.

The Uvicorn app, HTTP services, SQLite state, ports and control directories were
isolated fixtures. This was NOT a full production-app boot, real Chroma, real
Hermes inference, a reboot test, a different-user logon test or a remote-machine
pipe test. No production process was stopped, no model/runtime was installed,
and no GPU work ran. Windows ACL/pipe flags enforce the unexercised access cases;
they are not reported as separate behavioral tests.

The first legacy rehearsal encountered Windows error 5 on a pinned handle that
had already exited during chain teardown. The test now checks the handle's exit
code and skips already-dead targets; the narrow legacy rerun passed. No extra
privilege, broader kill or permission bypass was used. Failure history remains
in the evidence bundle.

## First activation — separate production approval REQUIRED

The old process cannot acquire a new cooperative hook from a Git merge. There
is no existing proven app-only graceful stop channel in the hidden legacy chain.
The verified one-time transition therefore requires an explicit maintenance
exception, or an ordinary separately planned system maintenance shutdown.

The exception was rehearsed ONLY on an isolated legacy process family with the
same risky supervisor-finally cleanup pattern:

1. Export/backup the scheduled-task definition and hidden-launcher configuration,
   application source and current Cookbook state. Require clean expected HEAD.
2. Outside all agent turns, establish a quiet window. Run all guards above against
   the LIVE old app, gateway and job inventories; any unknown/busy result aborts.
3. Resolve the exact old PowerShell launcher, optional venv redirector and Uvicorn
   process; verify ancestry, owner, creation identities, protected branches and
   shared-service health. Never reuse PIDs from this document. Open and verify
   process handles BEFORE any termination; recheck guards immediately beforehand.
4. ONLY WITH THE SEPARATE EXCEPTION APPROVAL, terminate the pinned app-specific
   PowerShell supervisor first (so its finally block cannot force-stop Chroma),
   then only its pinned app redirector/Uvicorn handles still alive. No `/T`,
   descendant sweep, console broadcast, gateway/Chroma termination, or unknown
   process target is permitted. A dead handle is skipped, not re-looked-up by PID.
5. Confirm old app/launcher exit and unchanged healthy gateway/Chroma. Start the
   reviewed normal launcher with `-RunOnly`. Verify the new control receipt,
   authenticated response, exact app listener identity and both shared services.
6. With explicit configuration approval, update the existing autostart action/
   hidden wrapper to include `-RunOnly`, preserving user/triggers/settings. Merely
   installing the code does NOT modify the existing scheduled task or VBScript;
   leaving the old task unchanged would boot the old installer/launch path.
7. Perform the short retained-Gemma status/log/persistence check without downloads.
   Preserve the refreshed state snapshot; never restore it over newer task data.

The operational restart CLI cannot perform step 4. The only force implementation
is in the isolated legacy test, whose fixture refuses production ports and pins
its own newly created family. No production bootstrap helper, task modification,
code integration or exception was executed as part of this development task.

## Integration and rollback

The final single-commit integration branch is
`integration/windows-restart-ready-164861ef`. It supersedes the initial
`0fbeab22` prototype; do not deploy that earlier prototype. Development history
is retained separately. The candidate remains based directly on `164861ef`.

Review this standalone restart commit against production. It does not include
model-serving lifecycle code or change HTTP/native security/workspace grants.
Integration, scheduled-task changes and first activation each remain approval
gates. If production advances, repin inside the authorized clone; do not overwrite
concurrent work. Preserve the older installer path for explicit setup use, but
never silently fall back to it during controlled restart.

Use a forward `git revert` for code rollback, never reset history or old task
state. Once running under the cooperative runner, rollback itself can use its
existing stop channel before re-entering the approved launcher version. If a
rollback removes that runner, it also removes future cooperative restart support;
plan the target startup mode explicitly rather than pretending the old hidden
process can be controlled by the new protocol.
