# JUNIPERUS090 — Memory / Skills / Model Routing

## Purpose

JUNIPERUS090 connects the Full Operator Loop to a governed routing layer for project context, reusable skills, and model selection.

It does not turn memory into an uncontrolled write target. It creates a controlled routing desk:

- choose project context intentionally;
- recommend reusable skills from a registry;
- choose model routes by risk/cost/privacy needs;
- require human approval before memory mutation, skill mutation, or paid/external model routing;
- keep secrets out of memory;
- preserve receipts and Mission Control state.

## Canonical roots

Workspace root:

```text
C:\Users\iamcy\CymaticsDev
```

Juniperus repo:

```text
C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus
```

## New route

After restart:

```text
http://127.0.0.1:7010/gnexus/memory-routing
```

## Boundary

JUNIPERUS090 is local and governed.

It does not:

- auto-write memories;
- auto-create or publish skills;
- auto-switch to paid/API models;
- call connectors;
- store secrets;
- expose vaults;
- mutate project code;
- execute shell commands.

## Relationship to previous packages

- JUNIPERUS010 established governed-power doctrine.
- JUNIPERUS020 created the App Dock.
- JUNIPERUS030 created the Approval Desk.
- JUNIPERUS040 intercepted shell/file power.
- JUNIPERUS050 created the diff-first editing gate.
- JUNIPERUS060 created approved patch apply with rollback snapshots.
- JUNIPERUS070 added verifier/repair/rollback loops.
- JUNIPERUS080 connected the full operator loop.
- JUNIPERUS090 teaches the operator loop how to route context, skills, and models.

## Next package

JUNIPERUS100 — Controlled Write / Live Activation Finalizer.
