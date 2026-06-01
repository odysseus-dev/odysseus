# JUNIPERUS060 — Approved Patch Apply + Rollback Snapshot Executor

## Purpose

JUNIPERUS060 adds the controlled executor that can apply a previously approved diff/patch proposal from the Juniperus Diff Gate.

It is not a raw write layer. It is a governed apply layer.

## Operating law

1. A file edit must first be proposed as a diff.
2. The proposal must be reviewed through the approval desk.
3. The apply endpoint only accepts approved patch objects.
4. Before writing, the executor creates a rollback snapshot.
5. After writing, it records an apply receipt.
6. Failed writes become repair items instead of silent mutations.

## Boundary

- Workspace root: `C:\Users\iamcy\CymaticsDev`
- Default Juniperus repo: `C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus`
- No external connector calls.
- No network calls.
- No secrets are stored.
- No app launch execution is unlocked by this package.
- No patch is auto-applied on install.

## New surfaces

- `/gnexus/patch-apply`
- `/api/gnexus/patch-apply/state`
- `/api/gnexus/patch-apply/apply`

The apply endpoint is designed for explicit human-triggered use. It rejects unapproved patch objects and sensitive or out-of-workspace paths.

## Status target

`JUNIPERUS_APPROVED_PATCH_APPLY_ROLLBACK_EXECUTOR_READY_LOCAL_CLOSEOUT`
