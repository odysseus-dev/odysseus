# Juniperus Controlled Write / Live Activation Runbook

## Human approval law

No operation may move from proposal to execution unless the Approval Desk records a human decision.

## Mutation law

Every write must have:

1. workspace-safe path
2. diff/patch object
3. explicit approval
4. rollback snapshot
5. apply ledger record
6. verification request
7. receipt or repair item

## Live activation law

Live activation is a separate gate and is disabled by this package.

A future live activation package must prove:

- authenticated user is admin
- app is local-only or behind private network/reverse proxy
- connector credentials are stored outside repo
- secrets never enter receipts/logs
- external reads and writes have separate approvals
- rollback or compensating action path exists


## v0.1.1 PSOBJECTFIX repair note
This repair version uses safe PSCustomObject property insertion when updating finalizer, mission-control, and receipt ledgers.
