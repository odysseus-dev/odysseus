# Automation existing-conversation execution (Gate 1)

Date: 2026-09-08 (America/Los_Angeles)  
Probe: `python3 scripts/openhands_probe.py automation-existing-conversation --json`  
Selected branch: `continued_run`

Interactive Agent Server execution is the explicit exception for interactive
turns. Do not invent Odysseus orchestration. A production dispatcher is out of
scope. A narrow upstream Automation patch would be required before Automation
could own interactive turns.

This is not `distinct_run`. Live execution IDs were not invented.

## Pins

From `deploy/openhands/versions.env`:

| Component | Pin |
|---|---|
| Agent Server | `ghcr.io/openhands/agent-server:1.45.0-python@sha256:b0104980776ed6adbd6ce406636e08069087b4f11fa130bfd3548b965b262946` |
| Automation | `ghcr.io/openhands/automation:1.11.0` |
| Canvas | `ghcr.io/openhands/agent-canvas:1.16.0@sha256:ab194760cb46098641747b27c1e07458ab1bed439821af7a31c97133ca466bb8` |

Automation source tag `1.11.0` resolves to
`f66a340398cc5b47075067d56a98f9552fc042c3`.

## Live stack attempts

| Step | Result |
|---|---|
| `docker pull ghcr.io/openhands/automation:1.11.0` | Failed: GHCR `unauthorized` |
| `docker compose up -d --no-deps --pull never openhands-automation` | Failed: `No such image: ghcr.io/openhands/automation:1.11.0` |
| Local Automation image | Absent |
| Public mirror / authenticated pull | Not available (GitHub token has no GHCR package read) |
| Canvas image | Absent locally |
| Agent Server image | Present (Task 1 pull) |
| `compose up --no-deps openhands-agent-server` | Container created, then crash-looped: overlay `command: ["openhands-agent-server", "--host", "0.0.0.0"]` is rejected as `unrecognized arguments: openhands-agent-server` (image entrypoint already invokes the binary). Service was stopped after observation. No conversation ID was created. |

No Automation HTTP API was reachable. Ports remain unpublished.

## API calls (documented Automation 1.11.0)

Sources fetched over HTTPS with `curl` after CPython `urllib` failed
`SSL: CERTIFICATE_VERIFY_FAILED` (no credentials):

| File | SHA-256 | Bytes |
|---|---|---|
| `router.py` | `5bca9659c804bf190b498667cb8966a646ff70e3cb8f26ffbdba00616fefaf69` | 34280 |
| `schemas.py` | `60e403ce5df8e41f0f94bd5346e81013118ae92878c4f660672109cae9d5c612` | 35958 |
| `conversations.py` | `5c25d3567d8d3be69983758b2fc66546bc4752d45ca2d5039795571ff57e400f` | 10567 |
| `models.py` | `ab0fb236636ddb495b02b64e6915e9d70ac13f830d0b39b836f86c1b45a72138` | 25029 |

URLs are `https://raw.githubusercontent.com/OpenHands/automation/1.11.0/openhands/automation/<file>`.

| Call | Observation |
|---|---|
| `POST /api/automation/v1/{automation_id}/dispatch` | No request body. Handler takes `automation_id`, `Request`, auth, and DB session only. No `conversation_id`, prompt, or `request_key`. Persisted enabled definition is mandatory. |
| `POST /api/automation/v1/runs/{run_id}/cancel` | Exact-run cancel exists for pending/running. Missing run → 404. Terminal → 409. Not exercised: no run ID was observed. |
| Event `destination: continue_conversation` | Derives conversation UUID from `(org_id, automation_id, source, subject_key)`. Successful follow-up reports `runs_created=[]` and `conversations_continued=[derived_id]`. No new Automation execution. |
| `CreateAutomationRequest` | `extra="forbid"`; no caller-selected `conversation_id`. |
| `AutomationRun` / `AutomationRunResponse` | `conversation_id` is a completion-callback output, not launch input. No `request_key` column or field. |

## Returned IDs

| ID | Value |
|---|---|
| first `execution_id` | unobserved (`null`) |
| second `execution_id` | unobserved (`null`) |
| first `conversation_id` | unobserved (`null`) |
| second `conversation_id` | unobserved (`null`) |

Reason: Automation never started. IDs were not invented. Distinct-run identity
therefore could not be demonstrated.

## Idempotency

Not supported. There is no request/idempotency key on dispatch or on
`AutomationRun`. Repeating `Idempotency-Key` is not a documented contract.
Live replay was not possible.

## Cancellation

Documented exact-run endpoint exists. Live cancel was not exercised because no
run ID existed. The identity contract's cancel assertions failed with status
`unobserved`.

## Conversation origin

Canvas-created targeting was not observed: Canvas image is not local, and
Automation 1.11.0 accepts no arbitrary `conversation_id`. Continue-path IDs are
derived, not caller-selected. Agent Server conversation create was not reached
(overlay command crash). Origin: unobserved.

## Persisted definition

Required. There is no definitionless ad-hoc dispatch. Creating a disposable
Automation just to send an interactive turn would be Odysseus orchestration and
is rejected.

## Selected branch

`continued_run`

Gate 1 does not pass as shipped. Interactive turns must use the documented
Agent Server execution path as an explicit exception unless a narrow upstream
Automation API adds definitionless dispatch with caller-selected
`conversation_id`, a distinct execution ID, stable request key, and exact-run
cancellation.

## Classifier

`probe_automation_existing_conversation()` returns
`ProbeResult(name="automation-existing-conversation", passed=False)` with
`classification=continued_run`. `passed` is true only for `distinct_run`.
