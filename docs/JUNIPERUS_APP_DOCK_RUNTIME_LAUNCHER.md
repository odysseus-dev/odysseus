# JUNIPERUS020 — App Dock + CymaticsDev Runtime Launcher

## Purpose

This layer turns Juniperus into a visible operations console surface for the
`C:\Users\iamcy\CymaticsDev` workspace.

It creates a governed App Dock that can discover local projects and internal tools,
classify launch/verify/open candidates, and produce launch approval requests.

## Boundary

This package does not grant raw launch power.

Runtime actions are still governed:

1. Discover app/tool.
2. Create registry object.
3. Show app in App Dock.
4. Generate launch proposal.
5. Require human approval before execution.
6. Future package executes approved launch with logs, verifier, and receipts.

## Installed surfaces

- `/gnexus/app-dock`
- `/api/gnexus/app-dock/state`
- `/api/gnexus/app-dock/scan`
- `/api/gnexus/app-dock/propose-launch`

## App registry

The scanner writes:

- `data/gnexus/app-registry.json`
- `data/gnexus/app-dock/launch-queue.json`
- `data/gnexus/app-dock/runtime-sessions.json`
- `data/gnexus/mission-control/app-dock-state.json`

## Runtime policy

Launch commands are candidates only in this package. Unknown commands, shell
mutation, package installs, git mutation, deletes, and direct writes remain blocked
or approval-required under the governed-power doctrine installed by JUNIPERUS010.

## Closeout target

`JUNIPERUS_APP_DOCK_RUNTIME_LAUNCHER_READY_LOCAL_CLOSEOUT`
