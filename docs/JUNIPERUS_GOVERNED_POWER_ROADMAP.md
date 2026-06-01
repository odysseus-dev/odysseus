# Juniperus Governed Power Roadmap

## JUNIPERUS010 - Governed Power Binder

Status target:

```text
JUNIPERUS_GNEXUS_GOVERNED_POWER_BINDER_READY_LOCAL_CLOSEOUT
```

Adds the policy scaffold, governance module files, governance route, app scanner, receipts, and verifier.

## JUNIPERUS020 - App Dock + Runtime Launcher

Create a visible App Dock using `data/gnexus/app-registry.json`.

Cards need:

```text
Open, Start, Stop, Logs, Verify, Repair, Edit, Approval State
```

## JUNIPERUS030 - Approval Queue UI

Turn approval JSON into a real console desk.

## JUNIPERUS040 - Shell/File Governance Interceptor

Wrap direct shell and file write power.

No mutation command executes without approval.

## JUNIPERUS050 - Diff-First Code Editing Gate

Replace direct write flow with:

```text
read -> patch plan -> diff -> approve -> apply -> verify -> receipt -> rollback
```

## JUNIPERUS060 - Registry-Only Runtime Launcher

Apps launch only from known registry records or approved launch proposals.

## JUNIPERUS070 - Verifier + Repair Loop

Every action gets a verifier result and repair item when it fails.

## JUNIPERUS080 - Full Operator Loop

Natural-language request becomes a governed local operation.

## JUNIPERUS090 - Memory / Skills / Project Runbooks

Bind each project to purpose, runbook, verifier, prior failures, and preferred repair style.

## JUNIPERUS100 - Controlled Write Activation

Final local-live controlled closeout.
