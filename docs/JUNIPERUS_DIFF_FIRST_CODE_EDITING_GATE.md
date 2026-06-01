# JUNIPERUS050 — Diff-First Code Editing Gate

Status target:

`JUNIPERUS_DIFF_FIRST_CODE_EDITING_GATE_READY_LOCAL_CLOSEOUT`

## Purpose

JUNIPERUS050 turns raw code writes into reviewable patch proposals.

Before this pass, Juniperus could expose a file tool that writes a path directly. After this pass, the filesystem `write_file` tool is intercepted before the raw write path and converted into:

1. target path classification,
2. workspace boundary check,
3. sensitive-file screening,
4. old/new content comparison,
5. unified diff generation,
6. patch proposal creation,
7. queue entry for human approval.

## Boundary

This package does not auto-apply patches.

Patch apply is intentionally left locked for the next stage so that the console cannot approve and execute its own mutations in a single step.

## New surface

After restart:

`http://127.0.0.1:7010/gnexus/diff-gate`

## Next stage

JUNIPERUS060 should add approved patch execution with rollback snapshots, log capture, and post-apply verifier calls.
