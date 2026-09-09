# OpenHands confirmation to Odysseus ApprovalGrant (Gate 4)

Date: 2026-09-08 (America/Los_Angeles)  
Probe: `python3 scripts/openhands_probe.py confirmation-approval-grant --json`

OpenHands owns the pending ActionEvent and confirmation lifecycle.
Odysseus signs the ApprovalGrant. OpenHands never holds the Odysseus
signing key. MCP verifies the grant before any domain side effect.

No credentials or raw secret values are recorded here.

## Pins

Agent Server 1.45.0 OpenAPI:

| Item | Contract |
|---|---|
| Pending event | `ActionEvent.id` (stable ULID/UUID), `kind=ActionEvent`, `action`, `llm_response_id`, `parent_id` |
| Confirmation identity | `POST /api/conversations/{conversation_id}/events/respond_to_confirmation` with `ConfirmationResponseRequest{accept, reason}` |
| Policy | `POST /api/conversations/{conversation_id}/confirmation_policy` (`AlwaysConfirm` / `ConfirmRisky` / `NeverConfirm`) |
| Normalized arguments | `ActionEvent.action` arguments; canonical UTF-8 JSON, sorted keys, compact separators, then SHA-256 digest |
| Grant delivery | Odysseus issues grant after confirmed `event_id`; MCP consumes grant on resume |
| Upstream patch | Not required for ActionEvent identity or confirmation respond |

## In-process proof

Confirmation precedes MCP. Altered arguments, expiry, replay, cross-execution,
and rejected confirmation all deny before `mail.send`. After a valid grant,
exactly one domain call is recorded.

## Selected branch

Narrow adapter: capture `PendingActionEvidence` from ActionEvent, confirm via
Agent Server, sign grant in Odysseus, attach grant on MCP resume. No OpenHands
copy of the Odysseus signing key.
