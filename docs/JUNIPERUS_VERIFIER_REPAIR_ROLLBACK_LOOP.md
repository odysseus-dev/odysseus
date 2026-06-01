# JUNIPERUS070 - Verifier / Repair / Rollback Loop

This package adds the governed post-apply loop for Juniperus as the Gnexus Operations Console.

## Purpose

JUNIPERUS060 created approved patch application with rollback snapshots. JUNIPERUS070 adds the next layer:

1. Verification requests after approved changes.
2. Verification results ledger.
3. Repair queue entries when verification fails.
4. Rollback request records when recovery is needed.
5. Mission Control state for the verifier loop.

## Boundary

This package does not run arbitrary shell commands. It creates queue and ledger objects only. Actual execution remains approval-gated and belongs to later packages.

## Route

After restart:

```text
http://127.0.0.1:7010/gnexus/verifier-loop
```

API state:

```text
/api/gnexus/verifier-loop/state
```

## Operating Law

Apply is not closeout. A change becomes operational only when it has an approved patch record, rollback snapshot reference, verification request, verification result, repair item if failed, and closeout receipt if passed.
