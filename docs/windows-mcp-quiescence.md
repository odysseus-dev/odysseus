# Permanent native MCP quiescence (development candidate)

This is new-process instrumentation. It cannot instrument the running legacy
interpreter. No production integration, activation or startup change is implied.

## Authoritative boundary

`McpManager.call_tool` admits one tracked tool-call scope before dispatch and
releases it in `finally`, including response errors, transport exceptions,
cancellation and timeout. All five built-in groups (image, memory, RAG, email,
Playwright) use this path. Retries remain inside that scope. Low-level dispatch
without a live scope is rejected and records unknown activity. No tool arguments,
results, environment values or exception messages are stored in the registry.

`tool_calls_in_flight` counts admitted public tool-call executions.
`connection_scopes_in_flight` also tracks setup, including the independent HTTP
OAuth background task after the outer wait returns. `in_flight` totals those
scopes; nested connection setup is deliberately conservative, not a count of
physical network requests. A server process existing does not increment the
counter. An uninitialized registry is unknown, not zero known work.

A returned MCP error response establishes RPC completion. A raised transport
error, timeout or cancellation does NOT establish remote completion: local
in-flight accounting reaches zero but a separate sticky unknown record blocks
automatic restart. Reopening admission never clears that evidence. This is a
trusted application dispatch boundary, not a sandbox against arbitrary Python
code in the same process, and not a claim about an external server's independent
jobs after a completed RPC. Existing background-job/worker guards still apply.

## Restart sequence

The authenticated Windows coordinator keeps its same-user/kernel-identity/HMAC
and receipt checks. Before fencing it checks the existing non-MCP guards; known
MCP work may drain, but unknown work cannot. It then:

1. Fences native tool calls AND new connection attempts and pauses HTTP admission.
2. Observes existing MCP scopes and already admitted mutating requests until the
   bounded monotonic deadline. Observation does not cancel their work.
3. Rechecks worker inventories, active Hermes leases and protected services.
4. Asks the actual manager/connection owner tasks to close their transports.
5. Observes owned stdio process exits and rechecks all guards before requesting
   Uvicorn shutdown. It never automatically escalates to a process signal.

The SDK adapter is deliberately limited to the exercised MCP SDK **1.30.0** on
Windows. It observes actual process creation using context-local manager/server
ownership, records kernel creation identities, and retains process diagnostics.
It closes stdin normally but blocks the SDK's terminate/kill fallback, avoids
its live-process `aclose()` kill path, and disables kill-on-job-close only on
these newly created private MCP jobs. Other clients and shared services are not
adopted or signalled. Changed hooks/unsupported SDKs record unknown and block
automatic restart rather than claiming compatible teardown. Descendant identity
changes are not adopted over the original record. Ordinary non-Windows stdio
transport behavior is unchanged; the tool counter is platform-independent.

The request deadline is signed with the control request and bounded at the
server (at most 300 seconds of MCP observation). Client handshake/response and
old-process observation share the caller's deadline. An expired preparation
cannot later commit shutdown. Concurrent preparations are rejected even if the
first caller times out while a read-only guard query finishes.

## Failure behavior

- Before peer closure, a known drain timeout leaves the accepted call alive and
  restores admission; no replacement starts.
- Unknown completion, unsupported ownership or incomplete closure refuses app
  shutdown. HTTP admission reopens for diagnostics, but native MCP remains fenced
  once closure/uncertainty occurred. It does not silently clear unknown evidence.
- A stubborn stdio server is left alive and reported, not killed. A pending
  transport close can finish later; that does not authorize a delayed app stop.
- After successful peer closure, if a shared-service/turn guard changed, an
  explicit subsequent request may reuse the closed fence only while counters
  remain zero and there is no unknown evidence. All guards are evaluated again.
- A control-channel timeout reports the stop outcome as unconfirmed and starts
  no replacement. Read authenticated status before deciding what to do next.
- Sticky unknown work needs explicit operator investigation/recovery or an
  approved maintenance transition; no automatic reset/bypass is provided.

## Focused execution evidence

- Unit/dispatch and coordinator checks cover all five groups, response errors,
  cancellation, browser timeout, untracked dispatch, setup fencing, bounded drain,
  real coordinator preparation with explicitly fake peers, and recycled child
  identities. These are code-level tests, not production activity observations.
- Real isolated Windows Uvicorn/PowerShell/named-pipe/SDK peer checks cover a
  blocked stdio RPC finishing before shutdown and a stubborn EOF peer refusing
  exit. The latter remains alive after the SDK cleanup timeout; the app and
  protected HTTP stand-ins retain their identities. Releasing the fixture exits
  it naturally. No production service, model, GPU or network download is used.
- Prior Windows process, HTTP-health, retained Gemma and browser evidence is
  reused; this does not assert full real-app production activation or Linux
  process teardown has been exercised.

See `windows-first-activation-decision.md` for the separate legacy decision.
