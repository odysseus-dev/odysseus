# JUNIPERUS080 — Full Operator Loop

## Purpose

JUNIPERUS080 connects the governed Juniperus rooms into one operator workflow.

The loop is:

```text
user intent
→ workspace/app selection
→ operation plan
→ risk classification
→ approval request
→ diff/patch proposal when code changes are involved
→ approved patch apply with rollback snapshot
→ verifier request
→ verification result
→ repair queue or closeout receipt
→ Mission Control operator state
```

## Rooms connected

- Governance Console
- App Dock
- Approval Desk
- Shell/File Interceptor
- Diff Gate
- Patch Apply
- Verifier / Repair / Rollback Loop

## Boundary

This package creates the operator orchestration layer but does not remove human approval.

- Shell execution remains governed.
- File writes remain diff-first / approval-gated.
- Patch application remains explicit-confirm only.
- Rollback remains ledgered.
- External reads/writes remain false.
- Connector calls remain false.
- Secrets are not stored.

## Target route

After restart:

```text
http://127.0.0.1:7010/gnexus/operator-loop
```

## Closeout target

```text
JUNIPERUS_FULL_OPERATOR_LOOP_READY_LOCAL_CLOSEOUT
```
